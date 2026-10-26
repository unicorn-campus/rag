[목표]
특허법 PDF 문서를 임베딩하여 공용 벡터 DB 구축
[역할]
당신은 LangChain 전문 개발자임
[입력]
- 임베딩 대상 문서: data 디렉토리에 존재하는 PDF 문서
- RAG 교재: textbook/10.RAG.md
[처리]
- RAG 교재 이해
- 인덱싱 예제 개발
  - 파이프라인: PDF 로드 → 정제 → 청킹 → 임베딩 → 벡터 DB 저장
  - 임베딩 모델: cohere
  - 벡터 DB: ChromaDB (로컬 저장, vectordb 디렉토리에 영속화)
  - 청킹: RecursiveCharacterTextSplitter (chunk_size=800, chunk_overlap=200)
  - 청킹 구분자는 나에게 추천
  - 메타데이터: source(파일명), chunk_index, total_chunks, char_count 저장
  - 설치할 라이브러리는 requirements.txt에 작성
  - 환경변수 참조: .env
- 테스트 및 버그 Fix
- README.md 작성
  - 소스 코드 설명 (주요 함수, 처리 흐름)
  - 가상환경 설정 및 실행 방법
[출력]
- 프로그램: vector/indexing/(소스, requirements.txt, README.md)
- 벡터 DB: vectordb
[제약사항]
- MUST: 
  - requirements.txt 파일 내 주석은 영문으로 작성
  - 추가정보나 내 의사결정이 필요하면 반드시 나에게 요청 
  - LangChain 사용
  - context7 mcp 사용 
- 완료조건
  - 모든 출력 생성
  - 모든 테스트 통과 및 정상 작동 