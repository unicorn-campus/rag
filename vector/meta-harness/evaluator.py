"""후보 하네스 평가기.

후보는 폴더 하나에 `harness.py`가 있고 그 안에 `class Candidate(Step5)`가 정의된 형태임.
질문 목록 × k회를 실행해 실행 기록(runs.jsonl), 점수(scores.json), 트레이스 원본(traces/*.jsonl)을
후보 폴더에 남김. 제안자는 이 파일들을 grep·cat으로 읽음(논문: 요약보다 원본 트레이스가 중요).

- 기존 질문(Q*)은 vector/harness/eval_questions.score, hard 질문(H*)은 hard_questions.score_hard로 채점
- 병렬 실행 시 워커마다 후보 인스턴스를 따로 만듦(Step5가 트레이스 콜백을 인스턴스에 두기 때문)
- 실행 1회가 너무 오래 걸리면 끊고 eval_timeout으로 기록
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "harness"))
sys.path.insert(0, str(HERE))

import common  # noqa: E402
from common import RunRecord  # noqa: E402
from eval_questions import bad_tool_calls  # noqa: E402
from eval_questions import score as score_base  # noqa: E402
from hard_questions import score_hard  # noqa: E402
from splits import question  # noqa: E402

RUN_TIMEOUT = 240          # 초. 실행 1회 상한(하네스 자체 상한과 별개인 평가기 안전망)
WORKERS = 3                # Groq 분당 한도를 넘지 않는 선


def auto_reject(actions):
    """평가 중 사람 승인은 자동 거부(승인 전에는 실행되지 않는지 보는 목적)."""
    return [{"type": "reject", "message": "평가 중 자동 거부"} for _ in actions]


def load_candidate_class(cand_dir: Path):
    path = Path(cand_dir) / "harness.py"
    name = f"cand_{Path(cand_dir).name}_{int(time.time() * 1000)}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.Candidate


def score_any(rec: RunRecord, qid: str):
    q = question(qid)
    ok, fails = (score_hard(rec, q) if qid.startswith("H") else score_base(rec, q))
    return ok, fails, bad_tool_calls(rec, q)


def _summ(rows: list[dict]) -> dict:
    if not rows:
        return {}
    by_q: dict[str, list[bool]] = {}
    for r in rows:
        by_q.setdefault(r["qid"], []).append(r["passed"])
    n = len(rows)
    return {
        "runs": n, "questions": len(by_q),
        "pass_rate": round(sum(r["passed"] for r in rows) / n, 4),
        "pass_at_k": sum(any(v) for v in by_q.values()),
        "pass_hat_k": sum(all(v) for v in by_q.values()),
        "pass_hat_k_rate": round(sum(all(v) for v in by_q.values()) / len(by_q), 4),
        "by_q": {q: sum(v) for q, v in by_q.items()},
        "avg_input_tokens": round(sum(r["input_tokens"] for r in rows) / n),
        "avg_output_tokens": round(sum(r["output_tokens"] for r in rows) / n),
        "avg_model_calls": round(sum(r["model_calls"] for r in rows) / n, 2),
        "avg_seconds": round(sum(r["seconds"] for r in rows) / n, 2),
        "failures": dict(Counter(f for r in rows for f in r["failures"])),
        "unapproved_send": sum(1 for r in rows if "unapproved_send" in r["failures"]),
        "hallucination": sum(1 for r in rows if "hallucination" in r["failures"]),
    }


def summarize(rows: list[dict]) -> dict:
    base = [r for r in rows if r["qid"].startswith("Q")]
    hard = [r for r in rows if r["qid"].startswith("H")]
    return {"all": _summ(rows), "base": _summ(base), "hard": _summ(hard)}


def evaluate(cand_dir: Path, qids: list[str], k: int = 3, out_dir: Path | None = None,
             workers: int = WORKERS, label: str = "search", log=print) -> dict:
    """후보를 qids × k회 평가하고 out_dir(기본: 후보 폴더)에 기록함."""
    cand_dir = Path(cand_dir)
    out_dir = Path(out_dir or cand_dir)
    (out_dir / "traces").mkdir(parents=True, exist_ok=True)
    cls = load_candidate_class(cand_dir)
    local = threading.local()
    lock = threading.Lock()

    def agent():
        if not hasattr(local, "agent"):
            wid = threading.get_ident() % 10000
            local.agent = cls(approver=auto_reject, trace_file=out_dir / "traces" / f"{label}-w{wid}.jsonl")
        return local.agent

    def one(job):
        qid, trial = job
        q = question(qid)
        started = time.time()
        rec = agent().run(q.text, thread_id=f"{label}-{qid}-t{trial}")
        ok, fails, bad = score_any(rec, qid)
        row = {**rec.to_dict(), "qid": qid, "trial": trial, "passed": ok, "failures": fails,
               "bad_tool_calls": bad, "wall_seconds": round(time.time() - started, 1)}
        return row

    jobs = [(qid, t) for qid in qids for t in range(1, k + 1)]
    rows: list[dict] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="eval") as pool:
        futures = {pool.submit(one, j): j for j in jobs}
        for fut, (qid, trial) in futures.items():
            try:
                row = fut.result(timeout=RUN_TIMEOUT)
            except FutureTimeout:
                row = {**RunRecord(stage=-1, question=question(qid).text, error="eval_timeout").to_dict(),
                       "qid": qid, "trial": trial, "passed": False, "failures": ["api_error"], "bad_tool_calls": 0}
            except Exception as e:  # noqa: BLE001  (후보 코드 예외도 실패로 기록하고 계속)
                row = {**RunRecord(stage=-1, question=question(qid).text,
                                   error=f"{type(e).__name__}: {str(e)[:300]}").to_dict(),
                       "qid": qid, "trial": trial, "passed": False, "failures": ["api_error"], "bad_tool_calls": 0}
            with lock:
                rows.append(row)
            log(f"  {label} {qid} #{trial}: {'통과' if row['passed'] else '실패 ' + ','.join(row['failures'])}"
                f" (모델 {row['model_calls']}회, 토큰 {row['input_tokens'] + row['output_tokens']:,})")
    rows.sort(key=lambda r: (r["qid"], r["trial"]))
    scores = {"label": label, "k": k, "qids": qids, "seconds": round(time.time() - started, 1), **summarize(rows)}
    with open(out_dir / f"runs-{label}.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / f"scores-{label}.json").write_text(json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8")
    return scores


if __name__ == "__main__":
    import argparse

    common.setup_console()
    ap = argparse.ArgumentParser(description="후보 하네스 평가")
    ap.add_argument("cand_dir")
    ap.add_argument("--split", default="search")
    ap.add_argument("--qids", nargs="*")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--label")
    ap.add_argument("--out")
    a = ap.parse_args()
    from splits import SPLITS

    s = evaluate(Path(a.cand_dir), a.qids or SPLITS[a.split], a.k, Path(a.out) if a.out else None,
                 label=a.label or a.split)
    print(json.dumps({k: s[k] for k in ("all", "base", "hard")}, ensure_ascii=False, indent=1)[:3000])
