"""평가 질문 분할: 기존 20문항과 hard 20문항을 각각 search 12 / holdout 8로 나눔.

- search: 제안자(Claude Code)가 점수와 실행 기록을 볼 수 있는 질문
- holdout: 제안자가 보지 못하는 질문. 최종 비교에만 씀(아카이브에 넣지 않음)
유형이 양쪽에 고르게 들어가도록 나눔. 전송 질문(Q5·Q20)은 안전 확인을 위해 search에 둠.
기존 평가셋 파일은 고치지 않고 import만 함.
"""

from __future__ import annotations

from hard_questions import HARD_BY_ID as _HARD
from eval_questions import BY_ID as _BASE  # vector/harness/eval_questions.py (수정 없음)

BASE_SEARCH = ["Q1", "Q3", "Q5", "Q6", "Q8", "Q10", "Q12", "Q13", "Q15", "Q17", "Q18", "Q20"]
BASE_HOLDOUT = ["Q2", "Q4", "Q7", "Q9", "Q11", "Q14", "Q16", "Q19"]
HARD_SEARCH = ["H1", "H2", "H4", "H5", "H8", "H9", "H10", "H13", "H14", "H17", "H18", "H20"]
HARD_HOLDOUT = ["H3", "H6", "H7", "H11", "H12", "H15", "H16", "H19"]

SPLITS = {
    "search": BASE_SEARCH + HARD_SEARCH,
    "holdout": BASE_HOLDOUT + HARD_HOLDOUT,
    "all": list(_BASE) + list(_HARD),
    "hard_all": list(_HARD),
}


def question(qid: str):
    return _BASE[qid] if qid in _BASE else _HARD[qid]


def check() -> None:
    assert sorted(BASE_SEARCH + BASE_HOLDOUT) == sorted(_BASE), "기존 20문항 분할 누락·중복"
    assert sorted(HARD_SEARCH + HARD_HOLDOUT) == sorted(_HARD), "hard 20문항 분할 누락·중복"
    assert not set(SPLITS["search"]) & set(SPLITS["holdout"])
