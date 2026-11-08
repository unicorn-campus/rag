[목표]
- 기존 AI 에이전트의 하네스를 Meta-Harness 방식으로 한 단계 발전
- 모델은 고정, 하네스 코드만 변경. 제안자가 보지 않은 질문(holdout)에서 정확도·비용 개선 확인

[역할]
- 당신은 하네스 엔지니어임. 제안자(코딩 에이전트)가 도는 바깥 루프의 설계·구현·실행 담당

[맥락]
- Meta-Harness: 코딩 에이전트가 이전 하네스들의 코드·점수·실행 트레이스 원본을 파일로 읽고 다음 하네스를 제안하는 반복 탐색
- 요약보다 원본 트레이스가 효과 큼. 안전은 프롬프트가 아닌 코드로 강제

[입력]
- 대상 하네스: {하네스 코드 경로}  예) vector/harness/ (class Step5)
- 검색기: {검색기 코드·인덱스 경로}
  예) vector/hybrid-search/hybrid_rag.py, vectordb/, bm25/
- 평가셋·채점기: {경로}  예) vector/harness/eval_questions.py
- 대상 모델: {모델}  예) Groq openai/gpt-oss-120b
- 제안자: {코딩 에이전트·인증}  예) Claude Code(구독 로그인) + claude-agent-sdk
- Meta-Harness 가이드
  - 논문: https://arxiv.org/abs/2603.28052
  - 적용 예제(README.md, PLAN.md, report-r1.md):
    https://github.com/unicorn-campus/rag/tree/main/vector/meta-harness
- 예산: {반복 수, 제안자 토큰 상한(호출당·전체)}

[처리]
1. 천장 효과 확인: 기존 점수가 높으면 어려운 질문 세트를 새 파일로 작성. 정답은 원문으로 확인, 기준 pass^k 60% 이하일 때 확정
2. 질문 분할: search(제안자 공개) / holdout(제안자 비공개). 유형을 양쪽에 고르게
3. 아카이브 구성: 후보 폴더마다 harness.py·rationale.md·점수·실행 기록·트레이스 원본. cand_000 = 현재 하네스
4. 제안자 격리: 읽기는 아카이브 안, 쓰기는 이번 후보 폴더만(훅으로 강제). 설정 파일 미로딩, 짧은 지침 제공
5. 안전 게이트: 정적 검사(금지 import·파일 쓰기·질문 하드코딩) + 가짜 LLM 테스트(승인 대기·도메인 차단·반복 상한). 통과 후보만 평가
6. 반복: 제안 → 게이트 → search 평가(k=3) → 기록 → 최선 후보군(Pareto frontier: 정확도↑·비용↓) 갱신. 부모는 제안자 선택
7. 마무리: 최선 후보군 재평가로 최종 후보 선택 → holdout·전체 비교 → 과적합 점검
8. 단위 테스트, README.md, 보고서 작성

[출력]
- 실습 폴더: 코드, requirements.txt, README.md, 단위 테스트
- 아카이브(archive/<run>/)와 최종 평가(final/<run>/)
- 보고서: 반복 궤적, 최선 후보군, holdout 비교, 과적합 점검, 코드 diff

[제약조건]
- MUST: 모델·인덱스·채점기·안전 장치(사람 승인, 허용 도메인, 호출 상한, 트레이스) 고정
- MUST NOT: 기존 평가셋 수정, 제안자에게 holdout 노출, 안전 지표 위반 후보 채택
- 예산·구독 한도 도달 시 중단 후 사유 기록. 의사결정 필요 시 사용자에게 요청
- 완료 조건: 실제 실행 로그와 holdout 비교 결과 확보, 모든 테스트 통과

[예시]
- r1(제안자 Sonnet, 8회): holdout hard pass^k 4/8 → 7/8, 입력 토큰 4,833 → 3,888, 안전 지표 0
