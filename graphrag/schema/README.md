# 특허법 GraphRAG 스키마 설계

특허법 청크(`vectordb/`의 `patent_law` 컬렉션)를 GraphRAG로 인덱싱하기 전에, **역량질문에 답할 수 있는 그래프 스키마**를 설계하고 **채우기 시험**으로 검증하는 예제임.
최종 산출물 `schema.json`은 다음 단계(GraphRAG 인덱싱)에서 `GraphSchema.from_file()`로 그대로 불러 씀.

| 항목 | 값 |
|------|----|
| 라이브러리 | neo4j-graphrag 1.22 (`SchemaFromTextExtractor`, `LLMEntityRelationExtractor`, `GraphPruning`, `GraphSchema`) |
| LLM | Groq `openai/gpt-oss-120b` (OpenAI 호환 엔드포인트, `OpenAILLM(base_url=...)`) |
| 입력 | ChromaDB `vectordb/`(청크 191건), `.env`의 `GROQ_API_KEY`, `references/how-to-competency-question.md` |
| 최종 스키마 | 노드 타입 9 · 관계 타입 12 · 패턴 12 (`schema.json`, 설계서 `schema-design.md`, 그림 `schema-design.mmd`) |

## 용어

- **역량질문(competency question)**: 그래프가 답할 수 있어야 하는 질문 목록. 스키마의 범위를 정하고, 완성 뒤에는 검증 기준(시금석)이 됨
- **그래프 스키마(graph schema)**: 그래프에 담을 노드 타입(점의 종류)·관계 타입(선의 종류)·패턴(어떤 관계가 어떤 노드 사이에 오는지)의 약속
- **표본 / 시험용 청크**: 표본은 스키마 후보를 뽑는 데, 시험용은 채우기 시험에만 씀(서로 겹치지 않음)
- **채우기 시험(fill test)**: 스키마대로 시험용 청크에서 실제로 추출해, 사람이 확정한 기대 결과와 비교하는 시험
  - 재현율(recall): 기대 항목 중 뽑힌 비율. 기준 0.8 이상
  - 지어냄 비율(hallucination rate): 뽑힌 항목 중 기대에 없는 비율. 기준 0.1 이하
  - 역량질문 답 가능: 모든 역량질문의 패턴 경로가 추출 결과에서 이어짐

## 처리 흐름

```
step1_competency.py   기본 질문 4가지 + 표본 → LLM 역량질문 초안 ──▶ [사람 확정] competency-questions.md
        │
step2_split.py        장(제N장)별로 묶어 표본 20건 / 시험용 10건(무관 2건 포함) 나누기 → samples.json
        │
step3_candidates.py   표본마다 SchemaFromTextExtractor.run(text, examples=역량질문)을 따로 호출
        │             (앞 호출 결과를 다음 호출에 넣지 않음) → candidates/sample-{chunk_index}.json
step4_merge.py        노드·관계·패턴별 등장 표본 수 집계, 합칠 후보 표시 → candidates/merged.md
        │
step5_draft.py        후보 + 역량질문 → LLM 스키마 초안(+ 사람 검토 수정안) ──▶ [사람 확정] schema-draft.json
        │
step6_expected.py     시험용 청크의 기대 추출 결과 LLM 초안 ──▶ [사람 확정] fill-test-expected.json
        │
step7_fill_test.py    LLMEntityRelationExtractor → GraphPruning(스키마 밖 모두 불허) → 기대 결과와 비교
        │             (--repeat 3: 같은 설정 3회 반복 평균으로 판정) → fill-test-report.md
        │             미달이면 step5부터 다시(설명·속성·패턴 수정)
step8_finalize.py     최종 스키마 → schema.json(GraphSchema로 다시 불러와 검증), schema-design.md, schema-design.mmd
```

- **사람 확인 단계**: 역량질문, 스키마 초안, 기대 추출 결과. 각 스크립트는 확정 표시가 있는 파일만 다음 단계 입력으로 읽음
  - `competency-questions.md`: `- 상태: 확정`
  - `schema-draft.json`, `fill-test-expected.json`: `"status": "확정"`
- API Key 값은 화면·로그·파일에 남기지 않음(환경변수로만 전달)

## 실행 순서

```bash
pip install -r graphrag/schema/requirements.txt
```

```bash
cd graphrag/schema
python step1_competency.py                    # → 검토 후 competency-questions.md를 '상태: 확정'으로
python step2_split.py
python step3_candidates.py                    # 이미 있는 결과는 건너뜀(--force로 다시)
python step4_merge.py
python step5_draft.py --round 1               # → 검토 후 schema-draft.json의 status를 "확정"으로
#   사람이 고친 초안을 쓸 때: python step5_draft.py --round 1 --from work/schema-draft-r1.reviewed.json
python step6_expected.py                      # → 검토 후 fill-test-expected.json의 status를 "확정"으로
python step7_fill_test.py --round 1 --repeat 3
#   미달이면: python step5_draft.py --round 2  (직전 회차 결과를 LLM에 주어 고침) → 확정 → step7 --round 2
python step8_finalize.py --round 5            # 최종 판정에 쓴 회차
```

- 사전 검사: 벡터DB 컬렉션이 없거나 청크가 0건이면 `vector/indexing/README.md`대로 인덱싱하라고 안내하고 멈춤. `GROQ_API_KEY`가 비어 있어도 멈춤
- `step7_fill_test.py --rescore`: LLM을 다시 부르지 않고 저장된 추출 결과로 다시 채점(1회 측정 회차만)
- `step7_fill_test.py --schema-file work/...confirmed.json`: 지난 회차의 확정 초안으로 다시 시험

## 출력파일

| 파일 | 내용 |
|------|------|
| `competency-questions.md` | 기본 질문 4가지의 답, 확정 역량질문 9개, LLM 초안 대비 수정 내역 |
| `samples.json` | 표본 20건·시험용 10건의 `chunk_index`, 장, 시드(42), 나눈 규칙 |
| `candidates/sample-{chunk_index}.json` | 표본별 `SchemaFromTextExtractor` 원본 응답(`llm_raw`)과 GraphSchema 변환 결과 |
| `candidates/merged.md` · `merged.json` | 후보별 등장 수·속성·등장 표본 번호, 합칠 후보 묶음 |
| `schema-draft.json` · `schema-draft.md` | 최종으로 확정한 스키마 초안(2회차)과 후보 처리 근거, 역량질문별 패턴 경로 |
| `fill-test-expected.json` | 시험용 청크별 기대 노드·관계(사람 확정, 수정 이력 포함) |
| `fill-test-report.md` | 회차별 지표, 청크별 결과, 놓친·지어낸 항목, `NOT_IN_SCHEMA` 집계, 원인 추정, 최종 결정 |
| `schema.json` | 인덱싱용 GraphSchema JSON(한글은 유니코드 이스케이프. 라이브러리가 OS 기본 인코딩으로 읽기 때문) |
| `schema-design.md` | 사람이 읽는 설계서: 노드·관계 설명, 역량질문별 패턴 경로, 시험 결과, 한계와 권고 |
| `schema-design.mmd` | 스키마 그림(mermaid, 영문 이름 뒤 괄호에 한글 병기) |
| `work/` | 회차별 원자료(`round-N.json`), 회차별 초안(LLM 원본·검토 수정안·확정본), 이전 기대 결과, 시험에 쓴 보완 프롬프트, `schema-load.log` |

## 결과 요약과 한계

| 회차 | 측정 | 재현율 | 지어냄 비율 | 역량질문 |
|---|---|---|---|---|
| 1 ~ 4 | 1회 | 0.341 ~ 0.927 | 0.125 ~ 0.400 | 6 ~ 9/9 |
| **5 (최종 스키마)** | 3회 평균 | **0.870** | **0.294** | 8/9 |
| 6 · 7 | 3회 평균 | 0.679 · 0.833 | 0.369 · 0.343 | 9/9 · 8/9 |

- **기준을 넘은 회차는 없음.** 사용자 결정으로 가장 나은 2회차 스키마(5회차 측정)를 최종안으로 확정함
- 지어냄의 대부분은 무관 조문(전자문서 이용신고 등)의 절차, 고립 노드, 존속기간 연장 같은 범위 밖 기간, 조건문 형태의 이름 변형. 스키마 설명의 제외 규칙을 추출 LLM이 잘 따르지 않음
- **인덱싱 단계 권고**: 엔터티 관계가 없는 고립 노드를 지우는 후처리(저장 결과 계산상 지어냄 0.274 → 0.206). 더 낮추려면 청크가 역량질문 범위인지 판별하는 단계를 검토
- 추출 LLM은 temperature 0에서도 실행마다 결과가 크게 달라, 채우기 시험은 3회 반복 평균으로 판정함
- 자세한 내용: `schema-design.md` 7절, `fill-test-report.md`
