# 실험 색인 보고서: final

- 그래프: bolt://localhost:7688 (실험용, 원래 실습 그래프와 별도)
- 추출: 실험 방안 `final`(pipelines.py), 적재·병합·임베딩·커뮤니티는 원래 색인 함수 그대로

| 항목 | 결과 |
|---|---|
| 청크 | 191건 |
| 추출 엔티티·관계 | 446개 · 350개 |
| 병합 | 엔티티 446 → 254개 |
| 고립 엔티티 | 0개 |
| 엔티티 임베딩 | 254개, 1536차원 |
| 커뮤니티 | 21개(2개 이상 21개), 요약 21/21개 |
| 역량질문 패턴 경로 | 9/9 |

| 노드 라벨 | 개수 |
|---|---|
| __Entity__ | 254 |
| Chunk | 191 |
| Actor | 81 |
| Procedure | 62 |
| Period | 42 |
| Decision | 23 |
| Community | 21 |
| Exclusion | 12 |
| Invention | 10 |
| Remedy | 9 |
| Penalty | 8 |
| Right | 7 |

| 관계 타입 | 개수 |
|---|---|
| FROM_CHUNK | 446 |
| IN_COMMUNITY | 254 |
| CAN_FILE | 84 |
| HANDLED_BY | 54 |
| HAS_DEADLINE | 43 |
| TARGETS | 36 |
| HOLDS | 20 |
| PUNISHED_BY | 19 |
| CAN_CLAIM | 16 |
| NOT_EFFECTIVE_FOR | 15 |
| CLAIMED_AGAINST | 12 |
| NOT_ELIGIBLE_FOR | 7 |
| ELIGIBLE_FOR | 4 |
| HAS_DURATION | 4 |

| 역량질문 | 조회 건수 | 질문 대상 건수 |
|---|---|---|
| CQ01 | 4 / 7 | 4 / 7 |
| CQ02 | 7 | 7 |
| CQ03 | 84 | 10 |
| CQ04 | 79 | 26 |
| CQ05 | 43 | 12 |
| CQ06 | 4 | 4 |
| CQ07 | 19 | 2 |
| CQ08 | 15 | 12 |
| CQ09 | 16 | 7 |
