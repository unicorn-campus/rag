"""0~5단계 하네스를 같은 평가 질문 5개로 k번씩 돌려 비교표를 만듦 (교재 67p 결과물).

- 단계별 결과: results/stage-N.json (실행 기록 + 채점)
- 비교표: results/compare.md (있는 stage-N.json을 모두 모아 만듦. 단계를 나눠 돌려도 됨)
- 지표: 통과율, pass@k / pass^k(교재 56~57p), 토큰, 모델·도구 호출 수, 잘못된 도구 호출, 시간, 실패 유형
- 4·5단계의 사람 승인은 평가 중 자동 '거부'로 처리함(승인 전에는 실행되지 않는지 확인하는 목적)

실행:
    python evaluate_stages.py                    # 0~5단계 전부, k=3
    python evaluate_stages.py --stages 0 1 --k 1 # 일부 단계만
    python evaluate_stages.py --report-only      # 저장된 결과로 비교표만 다시 만듦
"""

from __future__ import annotations

import argparse
import importlib
import json
import time
from collections import Counter

import common
from common import RESULTS_DIR, RunRecord
from eval_questions import BY_ID, QUESTIONS, bad_tool_calls, score

STAGES = {
    0: ("step0_baseline", "Step0"),
    1: ("step1_limits", "Step1"),
    2: ("step2_context", "Step2"),
    3: ("step3_verify", "Step3"),
    4: ("step4_approval", "Step4"),
    5: ("step5_trace", "Step5"),
}
FAIL_TYPES = ["no_search", "missed_evidence", "wrong_answer", "wrong_refusal", "ungrounded", "hallucination",
              "over_refusal", "bad_tool_call", "unapproved_send", "runaway", "no_answer", "api_error"]
RECORD_FIELDS = set(RunRecord.__dataclass_fields__)


def rescore(rows: list[dict]) -> list[dict]:
    """저장된 실행 기록을 현재 채점기로 다시 채점함(채점 기준을 고쳐도 다시 돌리지 않아도 됨)."""
    for r in rows:
        rec = RunRecord(**{k: v for k, v in r.items() if k in RECORD_FIELDS})
        q = BY_ID[r["qid"]]
        ok, fails = score(rec, q)
        r.update(passed=ok, failures=fails, bad_tool_calls=bad_tool_calls(rec, q))
    return rows


def load_stage(n: int):
    module, cls = STAGES[n]
    return getattr(importlib.import_module(module), cls)


def run_stage(n: int, k: int, qids: list[str], pause: float) -> list[dict]:
    cls = load_stage(n)
    agent = cls()
    if hasattr(agent, "approver"):
        agent.approver = lambda actions: [{"type": "reject", "message": "평가 중 자동 거부"} for _ in actions]
    rows = []
    for q in [BY_ID[i] for i in qids]:
        for trial in range(1, k + 1):
            rec = agent.run(q.text, thread_id=f"s{n}-{q.qid}-t{trial}")
            ok, fails = score(rec, q)
            row = {**rec.to_dict(), "qid": q.qid, "trial": trial, "passed": ok, "failures": fails,
                   "bad_tool_calls": bad_tool_calls(rec, q)}
            rows.append(row)
            print(f"  {n}단계 {q.qid} #{trial}: {'통과' if ok else '실패 ' + ','.join(fails)} "
                  f"(모델 {rec.model_calls}회, 도구 {len(rec.tool_calls)}회, 토큰 {rec.input_tokens + rec.output_tokens:,}, "
                  f"{rec.seconds}초{', 중단=' + rec.stopped if rec.stopped else ''})", flush=True)
            time.sleep(pause)
    return rows


def summarize(n: int, rows: list[dict]) -> dict:
    by_q: dict[str, list[bool]] = {}
    for r in rows:
        by_q.setdefault(r["qid"], []).append(r["passed"])
    runs = len(rows)
    avg = lambda key: sum(r[key] for r in rows) / runs  # noqa: E731
    fails = Counter(f for r in rows for f in r["failures"])
    return {
        "stage": n, "title": load_stage(n).title, "runs": runs,
        "pass_rate": sum(r["passed"] for r in rows) / runs,
        "pass_at_k": sum(any(v) for v in by_q.values()), "pass_hat_k": sum(all(v) for v in by_q.values()),
        "questions": len(by_q), "by_q": {q: sum(v) for q, v in by_q.items()}, "k": max(len(v) for v in by_q.values()),
        "in_tok": avg("input_tokens"), "out_tok": avg("output_tokens"),
        "model_calls": avg("model_calls"), "tool_calls": sum(len(r["tool_calls"]) for r in rows) / runs,
        "bad_tool": sum(r["bad_tool_calls"] for r in rows), "seconds": avg("seconds"),
        "stopped": Counter(r["stopped"] for r in rows if r["stopped"]),
        "blocked": sum(1 for r in rows for e in r["events"] if e["kind"].startswith("blocked")),
        "verify_failed": sum(1 for r in rows for e in r["events"] if e["kind"] == "verify_failed"),
        "approval_wait": sum(1 for r in rows for e in r["events"] if e["kind"] == "approval_wait"),
        "sent": sum(len(r["sent"]) for r in rows),
        "fails": fails,
        "q5_tools": [len(r["tool_calls"]) for r in rows if r["qid"] == "Q5"],
    }


def write_report() -> str:
    stats = []
    for n in STAGES:
        path = RESULTS_DIR / f"stage-{n}.json"
        if path.exists():
            rows = rescore(json.loads(path.read_text(encoding="utf-8")))
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            stats.append(summarize(n, rows))
    if not stats:
        return ""
    lines = ["# 하네스 단계별 비교표", "",
             f"평가 질문 {stats[0]['questions']}개 × k={stats[0]['k']}회. 모델은 모든 단계에서 Groq `openai/gpt-oss-120b` 고정, 하네스만 바꿈.", "",
             "## 1. 점수·비용", "",
             "| 단계 | 장치 | 통과율 | pass@k | pass^k | 입력 토큰 | 출력 토큰 | 모델 호출 | 도구 호출 | 잘못된 도구 호출(합) | 시간(초) |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in stats:
        lines.append(f"| {s['stage']} | {s['title']} | {s['pass_rate']:.0%} | {s['pass_at_k']}/{s['questions']} | "
                     f"{s['pass_hat_k']}/{s['questions']} | {s['in_tok']:,.0f} | {s['out_tok']:,.0f} | {s['model_calls']:.1f} | "
                     f"{s['tool_calls']:.1f} | {s['bad_tool']} | {s['seconds']:.1f} |")
    lines += ["", "토큰·호출·시간은 실행 1회 평균. pass@k = k번 중 한 번이라도 통과한 질문 수, pass^k = k번 모두 통과한 질문 수.", "",
              "## 2. 질문별 통과 횟수", "", "| 단계 | " + " | ".join(q.qid for q in QUESTIONS) + " |",
              "|---|" + "---:|" * len(QUESTIONS)]
    for s in stats:
        lines.append(f"| {s['stage']} | " + " | ".join(f"{s['by_q'].get(q.qid, '-')}/{s['k']}" for q in QUESTIONS) + " |")
    lines += ["", "## 3. 실패 유형(건수)", "", "| 단계 | " + " | ".join(FAIL_TYPES) + " |", "|---|" + "---:|" * len(FAIL_TYPES)]
    for s in stats:
        lines.append(f"| {s['stage']} | " + " | ".join(str(s["fails"].get(f, 0) or "") for f in FAIL_TYPES) + " |")
    lines += ["", "## 4. 하네스 개입", "", "| 단계 | 하네스가 멈춤(사유별) | 근거 검증 실패(재생성) | 차단 이벤트 | 승인 대기 | 실제 전송(outbox) | Q5 도구 호출 수 |",
              "|---|---|---:|---:|---:|---:|---|"]
    for s in stats:
        stopped = ", ".join(f"{k} {v}" for k, v in s["stopped"].items()) or "-"
        lines.append(f"| {s['stage']} | {stopped} | {s['verify_failed']} | {s['blocked']} | {s['approval_wait']} | {s['sent']} | {s['q5_tools']} |")
    lines += ["", "## 실패 유형 설명", "",
              "- no_search: 문서를 검색하지 않고 답함 / missed_evidence: 검색했지만 정답 청크를 못 찾음",
              "- wrong_answer: 핵심 조문 번호가 답에 없음 / wrong_refusal: 답이 있는데 거절함",
              "- ungrounded: 검색 원문에 없는 조문 번호를 쓰거나 근거 없이 답함 / hallucination: 범위 밖 질문에 지어냄",
              "- over_refusal: Q5에서 찾은 범위까지의 부분 결과 없이 통째로 거절함",
              "- bad_tool_call: 실패한 도구 호출·요청 없는 전송·같은 검색 반복 / unapproved_send: 승인 없이 메일이 outbox에 생김",
              "- runaway: 스스로 멈추지 못해 LangGraph 기본 상한(10,007단계)에 걸림 / no_answer: 최종 답 없음 / api_error: API 오류", ""]
    text = "\n".join(lines)
    (RESULTS_DIR / "compare.md").write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    common.setup_console()
    parser = argparse.ArgumentParser(description="하네스 단계별 평가")
    parser.add_argument("--stages", type=int, nargs="+", default=list(STAGES))
    parser.add_argument("--k", type=int, default=3, help="질문마다 반복 횟수(LLM 비결정성)")
    parser.add_argument("--questions", nargs="+", default=[q.qid for q in QUESTIONS])
    parser.add_argument("--pause", type=float, default=1.0, help="실행 사이 대기(초), API 분당 한도 대비")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)

    RESULTS_DIR.mkdir(exist_ok=True)
    if not args.report_only:
        for n in args.stages:
            print(f"\n=== {n}단계: {load_stage(n).title} ===", flush=True)
            rows = run_stage(n, args.k, args.questions, args.pause)
            (RESULTS_DIR / f"stage-{n}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + write_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
