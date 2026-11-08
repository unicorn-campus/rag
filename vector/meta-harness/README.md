# Meta-Harness 실습: 현재 하네스를 한 단계 발전시키기

교재 63p의 **Meta-Harness**(arXiv 2603.28052)를 `vector/harness`의 5단계 하네스(프롬프트 v3)에 적용한 예제입니다.
코딩 에이전트(로컬 Claude Code)가 이전 하네스들의 **코드·점수·실행 트레이스 원본**을 파일로 직접 읽고, 다음 하네스 코드를 제안합니다. 이 과정을 반복해 현재 하네스보다 나은 하네스를 찾습니다. 계획과 결정 사항은 [PLAN.md](PLAN.md)에 있습니다.

```
            ┌──────────────── 반복 i ────────────────┐
archive/ ──▶│ 제안자(Claude Code, 구독)                │
 cand_000   │   Read·Grep·Glob으로 코드·점수·트레이스 진단 │
 cand_001   │   cand_i/rationale.md → cand_i/harness.py │
 ...        │ 안전 게이트(코드) → 평가(search 24문항×3) │──▶ history.jsonl, frontier.json
            └──────────────────────────────────────────┘
최종: 최선 후보군 재평가 → holdout 16문항·전체 40문항으로 기준과 비교 → report-<run>.md
```

> **최선 후보군(Pareto frontier)**: 정확도(hard pass^k)와 비용(입력 토큰)을 함께 볼 때, 두 기준 모두에서 자신보다 나은 후보가 없는 후보들의 모음입니다. 더 정확한 후보와 더 싼 후보가 함께 남고, 다음에 발전시킬 부모는 이 안에서 고릅니다.

## 파일

| 파일 | 역할 |
|---|---|
| `hard_questions.py` | 어려운 질문 20개(hard set)와 채점기. 기존 평가셋 파일은 고치지 않고 채점 도구만 import |
| `splits.py` | 기존 20·hard 20을 각각 search 12 / holdout 8로 분할 |
| `evaluator.py` | 후보(`harness.py`의 `class Candidate(Step5)`)를 불러와 평가, 실행 기록·트레이스를 후보 폴더에 남김 |
| `safety_gate.py` | 정적 검사 + 가짜 LLM 안전 테스트. 통과해야 평가 |
| `proposer.py` | 로컬 Claude Code를 `claude-agent-sdk`로 헤드리스 실행. 읽기는 아카이브 안, 쓰기는 이번 후보 폴더 안만 허용(PreToolUse 훅) |
| `skill/PROPOSER.md` | 제안자 지침(논문의 짧은 skill) |
| `meta_loop.py` | 바깥 루프: `init`(기준 평가) → `loop`(제안 반복) → `final`(holdout·전체 비교, 보고서) |
| `report.py` | 보고서 `report-<run>.md` 생성 |
| `baseline/harness.py` | 기준 후보(cand_000): 현재 하네스 그대로 |
| `test_meta.py` | 단위 테스트: 게이트가 나쁜 후보 7종을 막는지, 경로 제한, hard 채점기, 최선 후보군 |

### 실행하면 생기는 파일 (아카이브·최종 평가)

`cand_XXX`는 `archive/r1/cand_000` ~ `cand_008`입니다. 제안자는 `archive/<run>/` 안만 읽을 수 있고, `final/<run>/`은 볼 수 없습니다.

| 파일·폴더 | 무엇인가 | 누가 쓰고 → 누가 읽나 |
|---|---|---|
| `cand_XXX/harness.py` | 후보 하네스 코드. `class Candidate(Step5)` 하나를 정의하고 바꾸고 싶은 메서드만 덮어씀 | 제안자 → 게이트·평가기 |
| `cand_XXX/rationale.md` | 후보를 만든 이유: 진단(근거가 된 qid와 기록), 가설, 바꾼 구조, 노리는 지표, 예상 위험 | 제안자 → 다음 제안자 |
| `cand_XXX/scores-search.json` | search 점수 요약: 기존·hard별 pass^k, pass@k, 통과율, 질문별 통과 횟수, 평균 토큰, 실패 유형 | 평가기 → 제안자 |
| `cand_XXX/runs-search.jsonl` | 실행 1회당 한 줄: 질문, 답, 실패 유형, 검색된 청크, 도구 호출, 하네스 사건, 토큰 | 평가기 → 제안자(Grep) |
| `cand_XXX/traces/*.jsonl` | OpenTelemetry span 원본(LLM 토큰·도구 요청, 도구 입력·결과 청크, 차단·승인 이벤트). 평가 워커마다 파일 하나 | 평가기 → 제안자(Grep) |
| `cand_XXX/gate.json` | 안전 게이트 결과(통과 여부, 탈락 이유) | meta_loop → 사람·제안자 |
| `cand_XXX/proposer.json` · `proposer_log.jsonl` | 제안자 턴·토큰·환산 비용 / 제안자가 쓴 도구 호출과 글(요약) | meta_loop → 사람 |
| `archive/<run>/history.jsonl` | 반복별 후보, 게이트 결과, 점수 요약, 제안자 사용량(한 줄 = 반복 1회) | meta_loop → 제안자 |
| `archive/<run>/frontier.json` | 최선 후보군 목록(hard pass^k ↑, 입력 토큰 ↓) | meta_loop → 제안자(부모 고르기) |
| `archive/<run>/lib/` | 하네스 라이브러리 원본 복사본(`step0`~`step5`, `common.py`, `hybrid_rag.py`). 평가 파일은 넣지 않음 | 제안자가 읽기만 |
| `final/<run>/<cand>/` | 최종 단계의 search 재평가(`search2`)·holdout·전체 평가 기록과 점수 | meta_loop → 사람 |
| `final/<run>/selection.json` | 최선 후보군 재평가 평균과 최종 선택 후보 | meta_loop → 사람 |
| `report-<run>.md` | 최종 보고서: 궤적, 최선 후보군, 선택, holdout 비교, 과적합 점검, 코드 diff | 사람이 읽음 |
| `logs/` · `work/hardcheck/` | 실행 로그 / hard set 확정 때 쓴 기준 하네스 측정(pass^k 9/20) | 사람이 읽음 |

## 안전 장치 (평가 점수를 위해 안전을 버리지 못하게)

- **제안자 격리:** 도구는 Read·Grep·Glob·Write·Edit뿐입니다. 아카이브 밖 읽기(holdout 질문·평가 파일)와 이번 후보 폴더 밖 쓰기는 훅이 막습니다. 사용자·프로젝트 설정도 읽지 않습니다.
- **API 과금 방지:** `ANTHROPIC_API_KEY`·`ANTHROPIC_AUTH_TOKEN`을 지운 뒤 실행해 claude.ai 구독으로만 돕니다.
- **안전 게이트:** 금지 import·파일 쓰기·평가 질문 하드코딩을 막고, 다음을 가짜 LLM으로 확인합니다.
  - 사람 승인(HITL) 유지
  - 허용 밖 도메인 차단
  - 반복 상한(모델 호출 12회 이내)
- **탈락 기준:** 승인 없는 전송이나 지어내기가 1건이라도 있거나, 기존 질문 pass^k가 기준보다 1문항 넘게 떨어지면 최선 후보군에 넣지 않습니다.
- **과적합 방지:** 제안자는 search 질문 결과만 봅니다. holdout은 `final/`에만 있습니다.

## 실행 (`vector/meta-harness`에서, `vector/harness`의 .venv)

```bash
../harness/.venv/Scripts/python -m pip install -r requirements.txt
../harness/.venv/Scripts/python meta_loop.py init  --run r1
../harness/.venv/Scripts/python meta_loop.py loop  --run r1 --iters 8 --model sonnet
../harness/.venv/Scripts/python meta_loop.py final --run r1
../harness/.venv/Scripts/python -m pytest -q
```

`loop`은 이어서 실행할 수 있습니다(이미 있는 후보는 건너뜀). 실행 후 `git restore vectordb/`로 인덱스 파일 변경을 되돌리세요.

## 결과 (run r1, 2026-11-08, 제안자 Sonnet, 반복 8회)

전체 보고서: [report-r1.md](report-r1.md)

| 평가 | 기준(cand_000 = v3) | 최종(cand_008) |
|---|---:|---:|
| holdout hard pass^k(제안자가 본 적 없음) | 4/8 | **7/8** |
| holdout 기존 pass^k | 7/8 | **8/8** |
| 전체 40문항 hard pass^k | 9/20 | **15/20** |
| 전체 40문항 hard 통과율 | 65% | **88%** |
| 전체 평균 입력 토큰 | 4,833 | **3,888** |
| 전체 평균 시간 | 3.2초 | 4.4초 |
| 안전 지표(승인 없는 전송·지어내기) | 0 | 0 |

- 제안자가 새로 만든 구조는 세 가지입니다.
  - 질문 분해 + 코드 사전 검색(prefetch)
  - 청크마다 실제 들어 있는 조문 번호 라벨
  - 조문 번호 직접 조회와 절차 링크 표
- 과적합 점검: holdout에서 개선된 hard 질문은 모두 링크 표에 걸리지 않는 질문입니다(링크 표 밖 hard 7문항 pass^k 3 → 6).
- 제안자 사용량: 8회 합계 토큰 1,482만(캐시 읽기 포함), 반복당 15~34턴. API 환산 $10.18이지만 구독 사용량이라 실제 청구는 없습니다.
- 반복 7에서 제안자가 아카이브 밖(작업 폴더, Claude 메모리 폴더)을 Grep하려다 경로 훅에 막혔습니다.

