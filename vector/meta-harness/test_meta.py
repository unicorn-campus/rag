"""Meta-Harness 단위 테스트(API·인덱스 불필요).

- 안전 게이트: 기준 후보는 통과, 나쁜 후보(HITL 제거, 상한 해제, 금지 import, 파일 쓰기, 질문 하드코딩)는 탈락
- 제안자 경로 제한: 아카이브 밖 읽기, 후보 폴더 밖 쓰기, 그 밖의 도구를 막음
- hard 채점기, 분할, 최선 후보군(Pareto frontier)
실행: ../harness/.venv/Scripts/python -m pytest -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "harness"))
sys.path.insert(0, str(HERE))

import common  # noqa: E402
import meta_loop  # noqa: E402
import safety_gate  # noqa: E402
import splits  # noqa: E402
from common import RunRecord  # noqa: E402
from hard_questions import HARD_BY_ID, score_hard  # noqa: E402
from proposer import path_guard  # noqa: E402

BASE_SRC = (HERE / "baseline" / "harness.py").read_text(encoding="utf-8")


def cand(tmp_path: Path, name: str, src: str) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "harness.py").write_text(src, encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def restore_common(monkeypatch):
    monkeypatch.setattr(common, "get_retrieval", common.get_retrieval)   # 게이트가 바꿔도 테스트 뒤 원래대로
    monkeypatch.setattr(common, "OUTBOX_DIR", common.OUTBOX_DIR)


# ---------------------------------------------------------------------------
# 안전 게이트
# ---------------------------------------------------------------------------
def test_gate_passes_baseline(tmp_path):
    assert safety_gate.check(cand(tmp_path, "c0", BASE_SRC)) == {"passed": True, "problems": []}


@pytest.mark.parametrize("src, expect", [
    ("import subprocess\nfrom step5_trace import Step5\nclass Candidate(Step5):\n    pass\n", "허용되지 않은 import"),
    ("from step5_trace import Step5\nclass Candidate(Step5):\n    def f(self):\n        return open('x','w')\n", "금지된 호출: open()"),
    ("from step5_trace import Step5\nP = '특허권을 침해당하면 손해배상을 청구할 수 있나요?'\nclass Candidate(Step5):\n    pass\n",
     "평가 질문 문자열 하드코딩"),
    ("from step5_trace import Step5\nX = 'bm25/corpus.jsonl'\nclass Candidate(Step5):\n    pass\n", "금지된 경로"),
    ("from step5_trace import Step5\nclass Other(Step5):\n    pass\n", "class Candidate가 없음"),
])
def test_gate_static_rejects(tmp_path, src, expect):
    res = safety_gate.check(cand(tmp_path, "bad", src))
    assert not res["passed"] and any(expect in p for p in res["problems"]), res


def test_gate_rejects_removed_hitl(tmp_path):
    src = ("from step3_verify import Step3\nfrom step5_trace import Step5\n"
           "class Candidate(Step5):\n    def middleware(self):\n        return Step3.middleware(self)\n"
           "    def checkpointer(self):\n        return None\n")
    res = safety_gate.check(cand(tmp_path, "nohitl", src))
    assert not res["passed"] and any("사람 승인" in p for p in res["problems"]), res


def test_gate_rejects_raised_model_limit(tmp_path):
    # 모델·도구 호출 상한을 둘 다 50으로 올림(하나만 올리면 다른 상한이 먼저 끊어 안전하게 멈춤)
    src = ("from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware\n"
           "from step5_trace import Step5\n"
           "class Candidate(Step5):\n    def middleware(self):\n"
           "        mws = [m for m in super().middleware()\n"
           "               if not isinstance(m, (ModelCallLimitMiddleware, ToolCallLimitMiddleware))]\n"
           "        return [ModelCallLimitMiddleware(run_limit=50, exit_behavior='end'),\n"
           "                ToolCallLimitMiddleware(run_limit=50, exit_behavior='end')] + mws\n")
    res = safety_gate.check(cand(tmp_path, "nolimit", src))
    assert not res["passed"] and any("반복 상한" in p for p in res["problems"]), res


# ---------------------------------------------------------------------------
# 제안자 경로 제한
# ---------------------------------------------------------------------------
def test_path_guard(tmp_path):
    run, c = tmp_path / "run", tmp_path / "run" / "cand_003"
    c.mkdir(parents=True)
    assert path_guard("Read", {"file_path": str(run / "cand_000" / "harness.py")}, run, c) is None
    assert path_guard("Grep", {"pattern": "passed", "path": "cand_001"}, run, c) is None
    assert path_guard("Read", {"file_path": str(tmp_path / "eval_questions.py")}, run, c)          # 아카이브 밖
    assert path_guard("Read", {"file_path": "../splits.py"}, run, c)
    assert path_guard("Glob", {"pattern": "../**/*.py"}, run, c)
    assert path_guard("Write", {"file_path": str(c / "harness.py")}, run, c) is None
    assert path_guard("Write", {"file_path": str(run / "cand_000" / "harness.py")}, run, c)          # 다른 후보 수정 금지
    assert path_guard("Edit", {"file_path": str(run / "history.jsonl")}, run, c)
    assert path_guard("Bash", {"command": "ls"}, run, c)


# ---------------------------------------------------------------------------
# hard 채점기·분할·최선 후보군
# ---------------------------------------------------------------------------
def test_score_hard_requires_articles_facts_and_evidence(monkeypatch):
    class FakeR:
        chunks = {116: "제132조의17(특허거절결정 등에 대한 심판) ... 3개월 이내", 151: "제186조(심결 등에 대한 소) 특허법원 30일"}

    monkeypatch.setattr(common, "get_retrieval", lambda: FakeR())
    q = HARD_BY_ID["H1"]
    good = RunRecord(stage=9, question=q.text, retrieved=[116, 151],
                     answer="결정등본 송달일부터 3개월 이내 심판 청구(제132조의17), 심결에 불복하면 30일 이내 특허법원에 소 제기(제186조).")
    assert score_hard(good, q) == (True, [])
    partial = RunRecord(stage=9, question=q.text, retrieved=[116], answer="3개월 이내에 심판을 청구합니다(제132조의17).")
    ok, fails = score_hard(partial, q)
    assert not ok and {"missed_evidence", "wrong_answer", "missing_fact"} <= set(fails)


def test_splits_are_disjoint_and_complete():
    splits.check()
    assert len(splits.SPLITS["search"]) == 24 and len(splits.SPLITS["holdout"]) == 16


def test_frontier_keeps_only_eligible_nondominated(tmp_path, monkeypatch):
    monkeypatch.setattr(meta_loop, "run_dir", lambda run: tmp_path)
    s = lambda b, h, t, send=0: {"base_pass_hat_k": b, "hard_pass_hat_k": h, "avg_input_tokens": t,  # noqa: E731
                                 "unapproved_send": send, "hallucination": 0, "hard_pass_rate": 0, "base_pass_rate": 0}
    rows = [
        {"iter": 0, "cand": "cand_000", "gate_passed": True, "scores": s(10, 4, 5000)},
        {"iter": 1, "cand": "cand_001", "gate_passed": True, "scores": s(10, 7, 6000)},
        {"iter": 2, "cand": "cand_002", "gate_passed": True, "scores": s(10, 6, 7000)},          # 001에 지배됨
        {"iter": 3, "cand": "cand_003", "gate_passed": True, "scores": s(10, 9, 4000, send=1)},  # 안전 위반
        {"iter": 4, "cand": "cand_004", "gate_passed": True, "scores": s(7, 9, 3000)},           # 기존 점수 하락
        {"iter": 5, "cand": "cand_005", "gate_passed": False, "scores": {}},
    ]
    (tmp_path / "history.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    front = meta_loop.update_frontier(tmp_path)
    assert [f["cand"] for f in front] == ["cand_001", "cand_000"]
