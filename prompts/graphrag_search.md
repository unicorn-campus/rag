[목표]  
질문에 따라 Local, Global, Hybrid 중 하나를 골라 Neo4j 지식그래프에서 검색하고 답하는 GraphRAG 검색 예제 개발

[역할]  
당신은 AI/ML 6년 경력에 GraphRAG와 프롬프트 엔지니어링 경험을 갖춘 AI 엔지니어입니다.  

[맥락]  
- 내 상황: `prompts/graphrag_index.md`로 특허법 지식그래프를 Neo4j에 구축함(엔티티 임베딩, 커뮤니티 요약 포함). 이제 이 그래프로 질문에 답함  
- 흐름: 에이전트가 스스로 판단하는 방식이 아니라, 아래 3단계를 매번 같은 순서로 도는 고정 워크플로우  
  - S1. 라우팅 → S2. 검색 → S3. 답변 생성  
- 검색 방식 3가지  
  - Local: 질문과 가까운 엔티티를 찾고, 그 주변 관계와 출처 청크를 근거로 씀. 특정 개념, 요건, 기간을 묻는 질문용  
  - Global: 질문과 가까운 커뮤니티 요약을 근거로 씀. 법 전체의 주제나 흐름을 묻는 질문용  
  - Hybrid: Local과 Global 결과를 함께 근거로 씀. 특정 대상을 전체 맥락 속에서 묻는 질문용  
- 결과물 독자: GraphRAG를 배우는 교육생

[입력]  
- 그래프: Neo4j `bolt://localhost:7687`, 비밀번호는 `neo4j/.env`의 `NEO4J_PASSWORD`  
- 벡터 인덱스: `entity_embedding`(엔티티), `community_embedding`(커뮤니티 요약)  
- 노드 라벨과 관계 이름: `graphrag/index/README.md`  
- 역량질문: `graphrag/schema/competency-questions.md`  
- API Key: `.env`의 `GROQ_API_KEY`, `COHERE_API_KEY`, `JEV_API_KEY`  
- 라이브러리: neo4j-graphrag, typesafe-sdk  
- 라우팅 모델(S1): TypeSafe Jev(`jev-latest`). 글 대신 정해진 선택지 중 하나를 고르는 결정 전용 모델. 사용법은 https://docs.typesafe.ai  
- 답변 LLM(S3): Groq, openai/gpt-oss-120b  
- 임베딩: Cohere embed-v4.0(색인과 같은 모델)

[처리]  
- 사전 검사  
  - Neo4j 접속이 안 되면 `neo4j/README.md`의 실행 방법을 안내하고 종료  
  - 벡터 인덱스 2개 중 없는 것이 있으면 `prompts/graphrag_index.md`를 먼저 수행하라고 안내하고 종료  
  - 비어 있는 API Key가 있으면 키 이름만 안내하고 종료  

- S1. 라우팅  
  - Jev를 1회 호출해 질문을 `local`, `global`, `hybrid` 중 하나로 고름  
  - 질문은 `state`에, 선택지는 `Choice` 질문의 `criteria`에 넣음. `criteria` 설명은 [맥락]의 검색 방식 3가지 설명을 그대로 씀  
  - 응답의 `choice`(고른 방식)와 `confidence`(확신도)를 함께 기록함  
  - 호출이 실패하면 `hybrid`로 처리함  
  - API Key는 `.env`의 `JEV_API_KEY`를 클라이언트에 직접 넘김(SDK 기본 변수 이름 `TYPESAFE_API_KEY`가 아님)  

- S2. 검색  
  - Local: `VectorCypherRetriever`로 `entity_embedding`에서 엔티티 상위 5개를 찾고, Cypher로 1단계 이웃 관계와 출처 청크 본문을 함께 가져옴  
  - Global: `VectorRetriever`로 `community_embedding`에서 커뮤니티 요약 상위 3개를 가져옴  
  - Hybrid: Local과 Global을 둘 다 돌려 결과를 이어 붙임  
  - 검색 결과가 0건이면 S3으로 넘기지 않고 "그래프에서 근거를 찾지 못함"으로 답함  

- S3. 답변 생성  
  - 검색 결과를 컨텍스트로, 질문과 함께 LLM을 1회 호출해 한국어로 답함  
  - 컨텍스트에 있는 내용만 쓰고, 답 끝에 근거(조문 번호나 엔티티 이름)를 붙이게 함  
  - 화면 출력: 질문, 선택된 검색 방식과 확신도, 답변. `--verbose`를 주면 검색된 컨텍스트도 보여 줌  

- 시험 질문 정하기  
  - 역량질문 전부에 Global형 2개, Hybrid형 2개를 LLM으로 더 써서 질문마다 기대 검색 방식을 붙임  
  - 시험 질문 목록을 나에게 보여 주고 확정받을 때까지 시험을 하지 않음  
  - 출력파일: `test-questions.md`  

- 시험  
  - 확정된 질문을 모두 돌려 질문별 선택된 검색 방식, 확신도, 기대 검색 방식과 일치 여부, 답변을 기록함  
  - 3가지 검색 방식이 각각 1회 이상 실제로 실행되었는지 확인함  
  - 출력파일: `test-report.md`(질문별 결과, 라우팅 일치 수)  

- 실행 스크립트 정리  
  - S1, S2, S3을 함수로 나누고, 함수 위에 어느 단계인지 주석을 닮  
  - 설치할 라이브러리는 `requirements.txt`에 작성  
  - `README.md`에 처리 흐름(S1 ~ S3), 검색 방식 3가지, 실행 방법, 옵션을 씀  


- README.md 작성
  - 목표 및 주요 기능
  - 소스 코드 설명 (주요 함수, 처리 흐름)
  - 가상환경 설정 및 패키지 설치: Windows(PowerShell, Git Bash), Mac/Linux 별로 작성    
  - 실행 방법 
  
- 결과 보고: 시험 질문 수, 라우팅 일치 수, 검색 방식별 실행 횟수, 근거를 찾지 못한 질문, 남은 한계

[출력]  
- 검색 프로그램: `graphrag/search/`(소스, `requirements.txt`, `README.md`)  
- 시험 질문: `graphrag/search/test-questions.md`  
- 시험 보고서: `graphrag/search/test-report.md`  
- 톤앤매너: 문서는 한국어 명사체, 교육생이 읽을 수 있게 용어를 처음 쓸 때 풀어 씀

[제약조건]  
- MUST:  
  - 추가정보나 내 의사결정이 필요하면 반드시 나에게 요청  
  - context7 mcp로 neo4j-graphrag 최신 사용법을 확인한 뒤 코드 작성. typesafe-sdk는 https://docs.typesafe.ai 에서 확인  
  - 질문 1건당 모델 호출은 S1 Jev 1회, S3 Groq 1회로 고정함  
  - `requirements.txt` 파일 내 주석은 영문으로 작성  
- MUST NOT:  
  - 그래프에 쓰거나 지우지 않음(읽기 전용)  
  - 컨텍스트에 없는 내용을 답에 넣지 않음  
  - API Key와 Neo4j 비밀번호 값을 화면, 로그, 파일에 남기지 않음  
- 완료조건:  
  - 모든 출력 생성  
  - 확정된 시험 질문을 모두 실행한 로그와 `test-report.md` 확인  
  - Local, Global, Hybrid가 각각 1회 이상 실행되어 답변이 나옴

[예시]  
S1 Jev 호출의 입력과 응답 형식(질문과 수치는 예시일 뿐임)

<라우팅예시>
입력
- state: "특허권의 존속기간은 언제까지인가?"
- questions.route: Choice
  - instructions: "이 질문에 답하려면 어떤 그래프 검색 방식이 맞는지 고르시오"
  - criteria:
    - local: "특정 개념, 요건, 기간을 묻는 질문"
    - global: "법 전체의 주제나 흐름을 묻는 질문"
    - hybrid: "특정 대상을 전체 맥락 속에서 묻는 질문"

응답(answers.route)
{"type": "choice", "choice": "local", "confidence": 0.91,
 "probabilities": {"local": 0.91, "global": 0.03, "hybrid": 0.06}}
</라우팅예시>
