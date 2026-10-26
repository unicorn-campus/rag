[목표]
LangChain 기반 Naive RAG 예제 개발
[역할]
당신은 LangChain 전문 개발자임
[입력]
- 벡터 DB: vectordb 
- RAG 교재: textbook/10.RAG.md
[처리]
- RAG 교재 이해
- 예제 개발
  - 인덱싱 새로 하지 말고 기존 벡터 DB를 로드하여 검색
  - 설치할 라이브러리는 requirements.txt에 작성
  - LLM 모델은 Groq LPU의 openai/gpt-oss-120b 모델 사용
  - 환경변수 참조: .env
- 테스트 및 버그 Fix
- README.md 작성
  - 소스 코드 설명 (주요 함수, 처리 흐름)
  - 가상환경 설정 및 실행 방법
[출력]
- 프로그램: vector/naive/(소스, requirements.txt, README.md)
[제약사항]
- MUST: 
  - requirements.txt 파일 내 주석은 영문으로 작성
  - 추가정보나 내 의사결정이 필요하면 반드시 나에게 요청 
  - LangChain 사용
  - context7 mcp 사용 
- 완료조건
  - 모든 출력 생성
  - 모든 테스트 통과 및 정상 작동 