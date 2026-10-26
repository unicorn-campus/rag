[목표]  
확정된 그래프 스키마로 특허법 청크에서 엔티티와 관계를 뽑아 Neo4j에 KG(지식그래프) 인덱스를 구축함

[역할]  
당신은 데이터 엔지니어링 7년 경력에 임베딩 파이프라인과 그래프 적재 경험을 갖춘 데이터 엔지니어입니다.  

[맥락]  
- 내 상황: 특허법 PDF를 청킹해 벡터DB에 넣었고, GraphRAG 스키마 설계(`graphrag/schema/`)도 끝냄. 이제 이 스키마로 그래프를 채움  
- 색인 방식: 전체 색인. 실행할 때마다 그래프를 비우고 처음부터 다시 만듦(증분 색인 없음)  
- 결과물 독자: 다음 단계 GraphRAG 검색 실습과 GraphRAG를 배우는 교육생

[입력]  
- 벡터DB: ChromaDB `vectordb/`, 컬렉션 `patent_law`  
- 스키마: `graphrag/schema/schema.json`, 역량질문별 패턴 경로는 `graphrag/schema/schema-design.md`  
- Neo4j: `neo4j/compose.yml`로 띄운 컨테이너, 접속 `bolt://localhost:7687`, 비밀번호는 `neo4j/.env`의 `NEO4J_PASSWORD`  
- API Key: `.env`의 `GROQ_API_KEY`, `COHERE_API_KEY`  
- 라이브러리: neo4j-graphrag  
- LLM: Groq, openai/gpt-oss-120b  
- 임베딩: Cohere embed-v4.0(벡터DB와 같은 모델)

[처리]  
- 사전 검사  
  - 벡터DB: 컬렉션이 없거나 청크가 0건이면 `vector/indexing/README.md`대로 인덱싱하라고 안내하고 종료  
  - 스키마: `schema.json`이 없거나 `GraphSchema`로 불러지지 않으면 `prompts/graphrag-schema.md`를 먼저 수행하라고 안내하고 종료  
  - API Key: 비어 있는 키가 있으면 키 이름만 안내하고 종료  
  - Neo4j: 접속이 안 되면 `neo4j/README.md`의 실행 방법을 안내하고 종료  

- 그래프 비우기(전체 색인 준비)  
  - 기존 노드가 있으면 노드 수를 보여 주고 내 확인을 받은 뒤 모두 지움  

- K1. 추출 단위 나누기  
  - 새로 자르지 않고 벡터DB 청크를 그대로 추출 단위로 씀(스키마 채우기 시험과 같은 단위)  
  - `chunk_index` 순으로 정렬하고, 청크마다 `chunk_index`, `source`, 본문을 넘김  

- K2. 추출: 엔티티와 관계 추출  
  - `LLMEntityRelationExtractor`에 청크와 스키마를 넣어 청크마다 노드와 관계를 뽑음  
  - 청크 노드와 "엔티티 → 청크" 연결도 함께 만듦(어느 청크에서 나온 엔티티인지 추적용)  
  - 실패한 청크는 건너뛰고 번호를 기록함. Groq 호출 한도(429)에 걸리면 기다렸다가 다시 시도함  

- K3. 가지치기: 결과 보관  
  - `GraphPruning.run(graph, schema)`로 스키마에 없는 노드, 관계, 속성을 지움. `additional_*` 3가지는 모두 `False`  
  - 정리된 결과를 청크별 JSON으로 저장함. 적재부터 다시 할 때 LLM을 또 부르지 않기 위함  
  - `pruning_stats`에서 지워진 항목을 사유별, 타입별로 셈  
  - 출력파일: `extracted/chunk-{chunk_index}.json`, `pruning-stats.json`  

- K4. 적재: Neo4j에 청크 넣기  
  - `Neo4jWriter`로 보관한 결과를 Neo4j에 씀(청크 노드, 엔티티 노드, 관계)  

- K5. 병합: 관련 엔티티만  
  - 엔티티 노드만 대상으로, 같은 타입이면서 이름이 같은 노드끼리 하나로 합침(`SinglePropertyExactMatchResolver`)  
  - 청크 노드는 합치지 않음. 타입이 다르면 이름이 같아도 합치지 않음  
  - 병합 전후 엔티티 수를 기록함  

- K6. 엔티티 설명 임베딩  
  - 엔티티마다 설명 문장을 만듦: `{타입}: {이름}. {설명 속성}`(설명 속성이 없으면 이름까지만)  
  - Cohere로 임베딩해 엔티티의 `embedding` 속성에 넣고, Neo4j 벡터 인덱스 `entity_embedding`을 만듦  
  - 벡터 차원은 정해 두지 않고 실제 임베딩 길이에서 읽음  

- (K7 청크 정리는 전체 색인이라 생략)  

- K8. 커뮤니티 요약  
  - GDS Leiden(`randomSeed` 42)으로 엔티티를 묶고, 묶음마다 커뮤니티 노드와 "엔티티 → 커뮤니티" 연결을 만듦  
  - 엔티티가 2개 이상인 커뮤니티만 LLM으로 한국어 요약 3문장 이내를 만들어 커뮤니티 노드에 저장함  
  - 요약을 Cohere로 임베딩해 커뮤니티의 `embedding` 속성에 넣고, Neo4j 벡터 인덱스 `community_embedding`을 만듦(Global 검색용)  

- 시범 실행: 전체 실행 전에 청크 5건만(`--limit 5`) 돌려 K1 ~ K6이 끝까지 도는지 먼저 확인함  

- 적재 검증  
  - 노드 타입별, 관계 타입별 개수를 셈  
  - 역량질문마다 `schema-design.md`의 패턴 경로를 Cypher로 조회해 1건 이상 나오는지 확인함  
  - 출력파일: `index-report.md`(단계별 건수, 실패 청크, 지워진 항목, 병합 전후 수, 역량질문별 조회 건수)  

- 실행 스크립트 정리  
  - 단계마다 함수를 나누고, 함수 위에 어느 단계(K1 ~ K8)인지 주석을 닮  
  - `graphrag/schema/`에 다시 쓸 수 있는 코드(청크 읽기 등)가 있으면 가져다 씀  
  - 설치할 라이브러리는 `requirements.txt`에 작성  
  - `README.md`에 처리 흐름(K1 ~ K8), 실행 순서, 옵션, 출력파일 설명을 씀  

- README.md 작성
  - 목표 및 주요 기능
  - 소스 코드 설명 (주요 함수, 처리 흐름)
  - 가상환경 설정 및 패키지 설치: Windows(PowerShell, Git Bash), Mac/Linux 별로 작성    
  - 실행 방법 

- 결과 보고: 처리한 청크 수, 실패 청크 수, 노드 타입별·관계 타입별 개수, 병합 전후 엔티티 수, 역량질문별 조회 결과, 남은 한계

[출력]  
- 실행 스크립트: `graphrag/index/`(소스, `requirements.txt`, `README.md`)  
- 추출 결과 보관: `graphrag/index/extracted/`  
- 가지치기 집계: `graphrag/index/pruning-stats.json`  
- 색인 보고서: `graphrag/index/index-report.md`  
- 그래프: Neo4j(`bolt://localhost:7687`)  
- 톤앤매너: 문서는 한국어 명사체, 교육생이 읽을 수 있게 용어를 처음 쓸 때 풀어 씀

[제약조건]  
- MUST:  
  - 추가정보나 내 의사결정이 필요하면 반드시 나에게 요청  
  - context7 mcp로 neo4j-graphrag 최신 사용법을 확인한 뒤 코드 작성  
  - 스키마는 `schema.json`을 그대로 불러 씀. 색인 중에 스키마를 고치지 않음  
  - `requirements.txt` 파일 내 주석은 영문으로 작성  
- MUST NOT:  
  - 내 확인 없이 Neo4j의 기존 데이터를 지우지 않음  
  - API Key와 Neo4j 비밀번호 값을 화면, 로그, 파일에 남기지 않음  
- 완료조건:  
  - 모든 출력 생성  
  - 전체 청크 색인 실행 로그와 `index-report.md` 확인  
  - 모든 역량질문의 패턴 경로가 Neo4j에서 1건 이상 조회됨

