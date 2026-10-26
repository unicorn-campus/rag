[목표]  
특허법 문서로 GraphRAG 인덱싱을 하기 위하여, 역량 질문에 답할 수 있는 그래프 스키마를 설계하고 채우기 시험으로 검증함

[역할]  
당신은 데이터 엔지니어링 7년 경력에 지식그래프 스키마 설계 경험을 갖춘 데이터 엔지니어입니다.  

[맥락]  
- 내 상황: 특허법 PDF를 청킹해 벡터DB에 인덱싱해 둠. 다음 단계인 GraphRAG 인덱싱 전에 그래프에 담을 스키마를 정해야 함  
- 결과물 독자: 다음 단계 GraphRAG 인덱싱 작업(스키마 파일을 그대로 불러 씀)과 GraphRAG를 배우는 교육생

[입력]  
- 벡터DB: ChromaDB `vectordb/` 
- API Key: `.env`의 `GROQ_API_KEY`  
- 역량질문 작성 가이드: `references/how-to-competency-question.md`  
- 실제 사용자 질문: 없음. 역량질문 초안은 LLM이 쓰고 내가 확정함  
- 라이브러리: neo4j-graphrag
- LLM: Groq, openai/gpt-oss-120b
 
[처리]  
- 사전 검사  
  - 벡터DB 확인: 컬렉션이 없거나 청크가 0건이면 `vector/indexing/README.md`대로 인덱싱하라고 안내하고 종료  
  - API Key 확인: `GROQ_API_KEY`가 비어 있으면 안내하고 종료  

- 청크 읽기: 벡터DB에서 청크 전체를 읽음
   
- 역량질문 정하기  
  - 역량질문 작성 가이드 이해  
  - 기본 질문 4가지(도메인, 용도, 답할 질문 유형, 사용자와 관리자)의 답 초안을 써서 나에게 확인받음  
  - 확인받은 용도와 사용자에 맞춰 질문 유형별 역량질문을 1개 이상 작성  
  - 역량질문 목록을 나에게 보여 주고 확정받을 때까지 다음 단계로 가지 않음  
  - 출력파일: `competency-questions.md`  

- 표본과 시험용 청크 나누기  
  - 청크 본문에 나오는 장 제목(`제N장`)으로 청크를 장별로 묶음. 장 제목이 없는 청크는 바로 앞 청크의 장을 따름  
  - 표본: 장마다 1건 이상, 전체 10 ~ 30건을 무작위로 뽑음(난수 시드 고정)  
  - 시험용: 표본과 겹치지 않게 5 ~ 10건을 따로 뽑음. 역량질문과 관계없는 청크(예: 부칙)를 2건 이상 넣음  
  - 출력파일: `samples.json`(표본과 시험용 청크의 `chunk_index`, 장, 시드)  

- 스키마 후보 넓게 뽑기  
  - 표본 청크마다 `SchemaFromTextExtractor.run(text=청크 본문, examples=역량질문 문자열)`을 따로 호출함  
  - `examples` 문자열 형식  
    - 첫 줄: "The schema must be able to answer the following competency questions:"  
    - 이어서 확정된 역량질문을 한 줄에 하나씩 `- ` 로 시작해 나열  
  - 모든 호출에 같은 역량질문 문자열만 넣음  
  - (중요) 앞 호출의 추출 결과를 다음 호출의 입력에 넣지 않음. 넣으면 앞 결과를 따라 써서 후보가 충분히 나오지 않음  
  - 출력파일: `candidates/sample-{chunk_index}.json`(호출별 원본 결과)  

- 스키마 후보 합치기  
  - 노드 타입, 관계 타입, 패턴별로 등장한 표본 수를 셈  
  - 이름이 다르고 뜻이 같아 보이는 후보는 합치지 않고 "합칠 후보"로 표시만 함  
  - 등장 수가 적은 후보도 이 단계에서는 지우지 않음  
  - 출력파일: `candidates/merged.md`(후보, 등장 수, 속성, 등장한 표본 번호)  

- 스키마 초안 확정  
  - 합친 후보 목록과 역량질문을 LLM에 주고 스키마 초안을 요청함  
  - LLM이 돌려줄 것  
    - 추가, 제거, 병합할 후보와 각각의 근거  
    - 노드 타입과 관계 타입마다 추출 기준이 되는 구체적 설명  
    - 역량질문마다 답에 쓰이는 패턴 경로(예: 노드 타입 → 관계 타입 → 노드 타입)  
  - 이름 규칙: 노드 타입은 영문 단수 PascalCase, 관계 타입은 영문 UPPER_SNAKE_CASE, 설명은 한국어  
  - 노드 타입마다 속성을 1개 이상 둠  
  - 초안을 나에게 보여 주고 확정받을 때까지 다음 단계로 가지 않음  

- 기대 추출 결과 만들기  
  - 시험용 청크마다 확정된 초안으로 뽑혀야 할 노드(타입, 이름)와 관계(시작, 관계 타입, 끝)를 LLM으로 초안 작성  
  - 역량질문과 관계없는 청크의 기대 결과는 "없음"으로 둠  
  - 기대 추출 결과를 나에게 확인받음. LLM이 쓴 기대 결과로 LLM 추출을 채점하지 않기 위함  
  - 출력파일: `fill-test-expected.json`  

- 채우기 시험  
  - 시험용 청크를 `LLMEntityRelationExtractor`에 스키마 초안과 함께 넣어 추출함  
  - 추출 결과를 `GraphPruning.run(graph, schema)`로 정리함. 스키마의 `additional_node_types`,  
    `additional_relationship_types`, `additional_patterns`는 모두 `False`로 둠  
  - 기대 추출 결과와 비교해 아래 3가지를 계산함. 노드는 타입과 이름이 같으면 일치, 관계는 시작, 타입, 끝이 같으면 일치  
    - 재현율: 기대 항목 중 뽑힌 항목 비율. 기준 0.8 이상  
    - 지어냄 비율: 뽑힌 항목 중 기대에 없는 항목 비율(관계없는 청크 포함). 기준 0.1 이하  
    - 역량질문 답 가능: 모든 역량질문의 패턴 경로가 추출 결과에서 1건 이상 이어짐  
  - `pruning_stats`에서 `NOT_IN_SCHEMA`로 지워진 항목을 타입별로 세어 함께 보고함(스키마에 빠진 후보의 신호)  
  - 3가지 기준 중 하나라도 못 넘으면 설명, 속성, 패턴을 고쳐 '스키마 초안 확정' 단계부터 다시 수행함. 최대 3회  
  - 3회 뒤에도 기준을 못 넘으면 멈추고 회차별 결과와 원인 추정을 나에게 보고함  
  - 출력파일: `fill-test-report.md`(회차별 지표, 지워진 항목 집계, 고친 내용)  

- 스키마 설계 확정  
  - 기준을 넘은 스키마를 인덱싱 단계에서 `GraphSchema`로 그대로 불러올 수 있는 JSON으로 저장함  
  - 노드 타입과 관계 타입의 설명, 역량질문별 패턴 경로는 사람이 읽는 설계서로 따로 저장함  
  - 출력파일: `schema.json`, `schema-design.md`  

- 실행 스크립트 정리  
  - 위 단계를 다시 돌릴 수 있게 스크립트로 남김. 사람 확인 단계는 확정된 파일을 읽어 이어 가게 함  
  - 설치할 라이브러리는 `requirements.txt`에 작성  
  - `README.md`에 처리 흐름, 실행 순서, 출력파일 설명을 씀  

- 결과 보고: 
  - 확정된 역량질문 수, 노드 타입과 관계 타입 수, 시험 회차별 3가지 지표, 남은 한계
  - mermaid 스크립트로 스키마 설계를 작성하여 나에게 보여줌: 영문안에 괄호로 한글 병기 

[출력]  
- 역량질문: `graphrag/schema/competency-questions.md`  
- 표본과 시험용 청크 목록: `graphrag/schema/samples.json`  
- 스키마 후보: `graphrag/schema/candidates/`  
- 기대 추출 결과: `graphrag/schema/fill-test-expected.json`  
- 채우기 시험 보고서: `graphrag/schema/fill-test-report.md`  
- 스키마 설계: `graphrag/schema/schema.json`, `graphrag/schema/schema-design.md`  
- 실행 스크립트: `graphrag/schema/`(소스, `requirements.txt`, `README.md`)  
- mermaid script: `graphrag/schema/schema-design.mmd` 
- 톤앤매너: 문서는 한국어 명사체, 교육생이 읽을 수 있게 용어를 처음 쓸 때 풀어 씀

[제약조건]  
- MUST:  
  - 추가정보나 내 의사결정이 필요하면 반드시 나에게 요청  
  - 역량질문, 스키마 초안, 기대 추출 결과는 내 확정을 받은 뒤 다음 단계로 감  
  - context7 mcp 사용  
  - `requirements.txt` 파일 내 주석은 영문으로 작성  
- MUST NOT:  
  - 스키마 후보 넓게 뽑기에서 앞 호출 결과를 다음 호출에 넣지 않음  
  - 표본 청크를 채우기 시험에 쓰지 않음  
  - API Key 값을 화면, 로그, 파일에 남기지 않음  
- 완료조건:  
  - 모든 출력 생성  
  - `fill-test-report.md`에 기준(재현율 0.8 이상, 지어냄 비율 0.1 이하, 역량질문 모두 답 가능)을 넘은 회차가 있음  
  - `schema.json`을 `GraphSchema`로 불러오는 실행 로그 확인

[예시]  
`examples`에 넣는 역량질문 문자열 형식(질문 내용은 예시일 뿐이며 실제 질문은 역량질문 단계에서 확정함)

<examples예시>
The schema must be able to answer the following competency questions:
- 특허를 받을 수 없는 발명은 어떤 것인가?
- 특허권의 존속기간은 언제부터 언제까지인가?
</examples예시>
