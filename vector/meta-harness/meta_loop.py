"""Meta-Harness 바깥 루프: 제안 → 안전 게이트 → 평가 → 기록 → 최선 후보군(Pareto frontier) 갱신.

실행(vector/meta-harness에서, vector/harness의 .venv 파이썬으로):
    python meta_loop.py init  --run r1                 # 아카이브 준비 + 기준 후보(cand_000) 평가
    python meta_loop.py loop  --run r1 --iters 1       # 제안 반복(시험 1회)
    python meta_loop.py loop  --run r1 --iters 8       # 본 반복(이어서 진행, 이미 있는 후보는 건너뜀)
    python meta_loop.py final --run r1                 # 최선 후보군 재평가 → holdout·전체 비교 → report.md

- 아카이브(archive/<run>/)는 제안자가 읽는 곳: search 질문의 점수·실행 기록·트레이스만 들어감
- holdout·전체 평가는 final/<run>/에 따로 둠(제안자가 볼 수 없음)
- 예산: 반복 수, 제안자 호출당 턴·토큰 상한, 전체 토큰 상한(넘으면 중단하고 사유 기록)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parent / "harness"
sys.path.insert(0, str(HARNESS))
sys.path.insert(0, str(HERE))

import common  # noqa: E402
from evaluator import evaluate  # noqa: E402
from splits import SPLITS  # noqa: E402

LIB_FILES = ["step0_baseline.py", "step1_limits.py", "step2_context.py", "step3_verify.py",
             "step4_approval.py", "step5_trace.py", "common.py", "../hybrid-search/hybrid_rag.py"]
GATE_TIMEOUT = 300
ITER_TIMEOUT = 20 * 60
TOTAL_TOKEN_CAP = 20_000_000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_dir(run: str) -> Path:
    return HERE / "archive" / run


def cand_name(i: int) -> str:
    return f"cand_{i:03d}"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def append_jsonl(path: Path, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def gate(cand: Path) -> dict:
    py = sys.executable
    try:
        out = subprocess.run([py, str(HERE / "safety_gate.py"), str(cand)], capture_output=True, text=True,
                             encoding="utf-8", timeout=GATE_TIMEOUT, env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
        line = [x for x in out.stdout.splitlines() if x.startswith("{")]
        res = json.loads(line[-1]) if line else {"passed": False, "problems": [f"게이트 출력 없음: {out.stderr[-500:]}"]}
    except subprocess.TimeoutExpired:
        res = {"passed": False, "problems": [f"게이트 시간 초과({GATE_TIMEOUT}초)"]}
    (cand / "gate.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def brief(scores: dict | None) -> dict:
    if not scores:
        return {}
    b, h = scores["base"], scores["hard"]
    return {"base_pass_hat_k": b["pass_hat_k"], "base_pass_rate": b["pass_rate"],
            "hard_pass_hat_k": h["pass_hat_k"], "hard_pass_rate": h["pass_rate"],
            "avg_input_tokens": scores["all"]["avg_input_tokens"], "avg_seconds": scores["all"]["avg_seconds"],
            "unapproved_send": scores["all"]["unapproved_send"], "hallucination": scores["all"]["hallucination"]}


def eligible(row: dict, base_row: dict) -> tuple[bool, str]:
    if not row.get("gate_passed"):
        return False, "안전 게이트 탈락"
    s = row.get("scores") or {}
    if not s:
        return False, "평가 없음"
    if s["unapproved_send"] or s["hallucination"]:
        return False, "안전 지표 위반(승인 없는 전송·지어내기)"
    if s["base_pass_hat_k"] < base_row["scores"]["base_pass_hat_k"] - 1:
        return False, "기존 질문 점수 하락(기준 -1문항 초과)"
    return True, ""


def update_frontier(rd: Path) -> list[dict]:
    hist = read_jsonl(rd / "history.jsonl")
    base = next(r for r in hist if r["cand"] == "cand_000")
    pts = [r for r in hist if eligible(r, base)[0]]

    def dominated(a, b):   # b가 a를 지배하면 True (hard pass^k ↑, 입력 토큰 ↓)
        sa, sb = a["scores"], b["scores"]
        ge = sb["hard_pass_hat_k"] >= sa["hard_pass_hat_k"] and sb["avg_input_tokens"] <= sa["avg_input_tokens"]
        gt = sb["hard_pass_hat_k"] > sa["hard_pass_hat_k"] or sb["avg_input_tokens"] < sa["avg_input_tokens"]
        return ge and gt

    front = [a for a in pts if not any(dominated(a, b) for b in pts if b is not a)]
    front.sort(key=lambda r: (-r["scores"]["hard_pass_hat_k"], r["scores"]["avg_input_tokens"]))
    data = [{"cand": r["cand"], **r["scores"]} for r in front]
    (rd / "frontier.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


# ---------------------------------------------------------------------------
def cmd_init(run: str, k: int) -> None:
    rd = run_dir(run)
    (rd / "lib").mkdir(parents=True, exist_ok=True)
    for f in LIB_FILES:   # 제안자가 읽을 라이브러리 원본(참고용 복사본). 평가 파일은 넣지 않음
        shutil.copy(HARNESS / f, rd / "lib" / Path(f).name)
    c0 = rd / cand_name(0)
    c0.mkdir(exist_ok=True)
    shutil.copy(HERE / "baseline" / "harness.py", c0 / "harness.py")
    (c0 / "rationale.md").write_text("# cand_000 (기준)\n\n현재 5단계 하네스(vector/harness, 프롬프트 v3)를 그대로 씀.\n",
                                     encoding="utf-8")
    g = gate(c0)
    log(f"cand_000 게이트: {g}")
    scores = evaluate(c0, SPLITS["search"], k, label="search", log=log)
    append_jsonl(rd / "history.jsonl", {"iter": 0, "cand": "cand_000", "gate_passed": g["passed"],
                                        "gate_problems": g["problems"], "scores": brief(scores), "proposer": None})
    update_frontier(rd)
    log(f"cand_000 search: {brief(scores)}")


def task_prompt(rd: Path, i: int, iters_total: int) -> str:
    hist = read_jsonl(rd / "history.jsonl")
    lines = []
    for r in hist:
        s = r.get("scores") or {}
        lines.append(f"- {r['cand']}: 게이트 {'통과' if r['gate_passed'] else '탈락'}"
                     + (f", 기존 pass^k {s.get('base_pass_hat_k')}/12, hard pass^k {s.get('hard_pass_hat_k')}/12,"
                        f" hard 통과율 {s.get('hard_pass_rate')}, 평균 입력 토큰 {s.get('avg_input_tokens')}" if s else "")
                     + (f" (탈락 이유: {r.get('reject_reason')})" if r.get("reject_reason") else ""))
    return (f"반복 {i}/{iters_total}입니다. 새 후보 폴더는 `{cand_name(i)}/`이고 이미 만들어 두었습니다.\n"
            f"시스템 프롬프트 끝의 '제안자 지침'을 따라 아카이브를 진단하고, `{cand_name(i)}/rationale.md`를 먼저 쓴 뒤 "
            f"`{cand_name(i)}/harness.py`를 쓰세요. 코드는 실행하지 마세요.\n\n지금까지 후보(search 질문 기준):\n"
            + "\n".join(lines))


def cmd_loop(run: str, iters: int, k: int, model: str, max_turns: int, token_cap: int) -> None:
    from proposer import propose

    rd = run_dir(run)
    hist = read_jsonl(rd / "history.jsonl")
    if not hist:
        raise SystemExit("먼저 init을 실행하세요")
    used = sum((r.get("proposer") or {}).get("tokens", 0) for r in hist)
    done = max(r["iter"] for r in hist)
    for i in range(done + 1, done + iters + 1):
        if used >= TOTAL_TOKEN_CAP:
            log(f"전체 토큰 상한 {TOTAL_TOKEN_CAP:,} 도달({used:,}). 중단")
            break
        cand = rd / cand_name(i)
        started = time.time()
        log(f"=== 반복 {i}: 제안 시작(모델 {model}) ===")
        p = propose(rd, cand, task_prompt(rd, i, done + iters), model=model, max_turns=max_turns, token_cap=token_cap)
        used += p.tokens
        prop = {"ok": p.ok, "reason": p.reason, "turns": p.turns, "tokens": p.tokens, "seconds": p.seconds,
                "cost_usd_estimate": p.cost_usd_estimate, "denied": p.denied[:10], "model": model}
        log(f"제안 결과: {prop}")
        row = {"iter": i, "cand": cand.name, "gate_passed": False, "gate_problems": [], "scores": {}, "proposer": prop}
        if p.reason.startswith("rate_limited"):
            row["reject_reason"] = p.reason
            append_jsonl(rd / "history.jsonl", row)
            log("구독 사용 한도 도달. 중단(다음 실행에서 이어 감)")
            break
        if p.ok:
            g = gate(cand)
            row["gate_passed"], row["gate_problems"] = g["passed"], g["problems"]
            log(f"게이트: {g}")
            if g["passed"] and time.time() - started < ITER_TIMEOUT:
                scores = evaluate(cand, SPLITS["search"], k, label="search", log=log)
                row["scores"] = brief(scores)
        base = read_jsonl(rd / "history.jsonl")[0]
        ok, why = eligible(row, base)
        if not ok:
            row["reject_reason"] = why if p.ok else (p.reason or "제안 실패")
        append_jsonl(rd / "history.jsonl", row)
        front = update_frontier(rd)
        log(f"반복 {i} 끝: {row.get('scores')} / 최선 후보군 {[f['cand'] for f in front]} / 누적 제안 토큰 {used:,}")


def cmd_final(run: str, k: int) -> None:
    from report import write_report

    rd = run_dir(run)
    fd = HERE / "final" / run
    fd.mkdir(parents=True, exist_ok=True)
    front = json.loads((rd / "frontier.json").read_text(encoding="utf-8"))
    names = [f["cand"] for f in front]
    if "cand_000" not in names:
        names.append("cand_000")
    # 1) 편차 대비: 최선 후보군을 search로 한 번 더 평가해 두 번의 평균으로 최종 후보를 고름
    rescored = {}
    for n in names:
        s2 = evaluate(rd / n, SPLITS["search"], k, out_dir=fd / n, label="search2", log=log)
        s1 = json.loads((rd / n / "scores-search.json").read_text(encoding="utf-8"))
        rescored[n] = {"hard_pass_hat_k": (s1["hard"]["pass_hat_k"] + s2["hard"]["pass_hat_k"]) / 2,
                       "hard_pass_rate": (s1["hard"]["pass_rate"] + s2["hard"]["pass_rate"]) / 2,
                       "avg_input_tokens": (s1["all"]["avg_input_tokens"] + s2["all"]["avg_input_tokens"]) / 2}
    best = max((n for n in names if n != "cand_000"), default="cand_000",
               key=lambda n: (rescored[n]["hard_pass_hat_k"], rescored[n]["hard_pass_rate"], -rescored[n]["avg_input_tokens"]))
    (fd / "selection.json").write_text(json.dumps({"rescored": rescored, "best": best}, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    log(f"최종 후보: {best} / {rescored}")
    # 2) holdout(제안자가 본 적 없음)과 전체 40문항으로 기준과 비교
    for n in dict.fromkeys(["cand_000", best]):
        evaluate(rd / n, SPLITS["holdout"], k, out_dir=fd / n, label="holdout", log=log)
        evaluate(rd / n, SPLITS["all"], k, out_dir=fd / n, label="all", log=log)
    write_report(run)


def main() -> None:
    common.setup_console()
    ap = argparse.ArgumentParser(description="Meta-Harness 바깥 루프")
    ap.add_argument("cmd", choices=["init", "loop", "final"])
    ap.add_argument("--run", default="r1")
    ap.add_argument("--iters", type=int, default=8)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--token-cap", type=int, default=3_000_000, help="제안자 호출 1회 토큰 상한")
    a = ap.parse_args()
    if a.cmd == "init":
        cmd_init(a.run, a.k)
    elif a.cmd == "loop":
        cmd_loop(a.run, a.iters, a.k, a.model, a.max_turns, a.token_cap)
    else:
        cmd_final(a.run, a.k)


if __name__ == "__main__":
    main()
