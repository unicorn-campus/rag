[목표]
Re-ranking 모델을 이용한 예제 개발
[역할]
당신은 RAG 기반 AI앱 개발 전문가임
[입력]
- 하이브리드 검색 예제: vector/hybrid-search/
- 벡터 DB: vectordb 
- BM25 인덱스: bm25
- RAG 교재: textbook/10.RAG.md

[처리]
- 하이브리드 검색 예제 분석 및 이해
- RAG 교재 이해
- 인덱싱 새로 하지 말고 기존 벡터 DB와 BM25 인덱스를 로드하여 검색
- 예제 개발
  - 질문 변환 여부를 실행 옵션으로 처리: rewrite, multi-query, HyDE, step-back, decomposition 기법을 사용하고 LLM에게 판단을 맡김    
  - Re-ranking 모델: Cohere rerank-multilingual-v3.0
  - 설치할 라이브러리는 requirements.txt에 작성 
  - LLM 모델은 Groq LPU의 openai/gpt-oss-120b 모델 사용 
  - 환경변수 참조: .env
- 테스트 및 버그 Fix
- README.md 작성
  - 목표 및 주요 기능
  - 소스 코드 설명 (주요 함수, 처리 흐름)
  - 가상환경 설정 및 패키지 설치: Windows(PowerShell, Git Bash), Mac/Linux 별로 작성    
  - 실행 방법 
- 기본RAG, 하이브리드 검색, 리랭킹(질문변환 없음), 리랭킹(질문변환 있음)의 품질 차이 비교 

[출력]
- 프로그램: vector/re-ranking/(소스, requirements.txt, README.md)
[제약사항]
- MUST:
  - (중요) requirements.txt 파일 내 주석은 영문으로 작성
  - 'EnsembleRetriever'는 langchain_classic.retrievers에 있는 것 사용 
  - 추가정보나 내 의사결정이 필요하면 반드시 나에게 요청 
  - LangChain 사용
  - context7 mcp 사용 
- 완료조건
  - 모든 출력 생성
  - 모든 테스트 통과 및 정상 작동 