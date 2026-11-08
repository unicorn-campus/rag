"""Meta-Harness 실행 보고서(report.md) 생성.

읽는 것: archive/<run>/history.jsonl·frontier.json·cand_*/rationale.md·harness.py, final/<run>/의 holdout·전체 평가
쓰는 것: vector/meta-harness/report-<run>.md
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _jl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def _j(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _row(name: str, s: dict | None) -> str:
    if not s:
        return f"| {name} | - | - | - | - | - | - | - |"
    b, h, a = s["base"], s["hard"], s["all"]
    return (f"| {name} | {b['pass_hat_k']}/{b['questions']} | {h['pass_hat_k']}/{h['questions']} | "
            f"{h['pass_at_k']}/{h['questions']} | {h['pass_rate']:.0%} | {a['avg_input_tokens']:,} | {a['avg_seconds']} | "
            f"{', '.join(f'{k} {v}' for k, v in sorted(h['failures'].items())) or '-'} |")


def write_report(run: str) -> Path:
    rd, fd = HERE / "archive" / run, HERE / "final" / run
    hist = _jl(rd / "history.jsonl")
    front = _j(rd / "frontier.json") or []
    sel = _j(fd / "selection.json") or {}
    best = sel.get("best", "")
    L = [f"# Meta-Harness 실행 보고서 ({run})", "",
         "제안자: 로컬 Claude Code(claude.ai 구독) · 대상 모델: Groq gpt-oss-120b(고정) · 기준: cand_000 = 현재 하네스(프롬프트 v3)", "",
         "## 1. 반복 궤적 (search 질문: 기존 12 + hard 12, 각 3회)", "",
         "| 반복 | 후보 | 게이트 | 기존 pass^k | hard pass^k | hard 통과율 | 평균 입력 토큰 | 제안자 턴 | 제안자 토큰 | 환산 비용(참고) | 비고 |",
         "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    total_tok = total_cost = 0
    for r in hist:
        s, p = r.get("scores") or {}, r.get("proposer") or {}
        total_tok += p.get("tokens", 0)
        total_cost += p.get("cost_usd_estimate", 0)
        L.append(f"| {r['iter']} | {r['cand']} | {'통과' if r['gate_passed'] else '탈락'} | "
                 f"{s.get('base_pass_hat_k', '-')}/12 | {s.get('hard_pass_hat_k', '-')}/12 | "
                 f"{s.get('hard_pass_rate', 0):.0%} | {s.get('avg_input_tokens', '-')} | {p.get('turns', '-')} | "
                 f"{p.get('tokens', 0):,} | ${p.get('cost_usd_estimate', 0):.2f} | "
                 f"{r.get('reject_reason') or '; '.join(r.get('gate_problems') or [])[:80] or '-'} |")
    L += ["", f"제안자 누적 토큰 {total_tok:,} · 환산 비용 합계 ${total_cost:.2f}(구독 사용량 참고치, 실제 청구 아님)", "",
          "## 2. Pareto 전선 (hard pass^k ↑, 입력 토큰 ↓)", "",
          "| 후보 | hard pass^k | 기존 pass^k | 평균 입력 토큰 |", "|---|---:|---:|---:|"]
    L += [f"| {f['cand']} | {f['hard_pass_hat_k']}/12 | {f['base_pass_hat_k']}/12 | {f['avg_input_tokens']:,} |" for f in front]
    if sel:
        L += ["", "## 3. 최종 후보 선택 (전선 후보를 search로 한 번 더 평가한 두 번의 평균)", "",
              "| 후보 | hard pass^k 평균 | hard 통과율 평균 | 입력 토큰 평균 |", "|---|---:|---:|---:|"]
        L += [f"| {n}{' **(선택)**' if n == best else ''} | {v['hard_pass_hat_k']:.1f}/12 | {v['hard_pass_rate']:.0%} | "
              f"{v['avg_input_tokens']:,.0f} |" for n, v in sel["rescored"].items()]
        L += ["", "## 4. 최종 비교: 제안자가 보지 않은 질문(holdout)과 전체", "",
              "| 평가 | 후보 | 기존 pass^k | hard pass^k | hard pass@k | hard 통과율 | 평균 입력 토큰 | 평균 시간(초) | hard 실패 유형 |",
              "|---|---|---:|---:|---:|---:|---:|---:|---|"]
        for label, title in (("holdout", "holdout(기존 8 + hard 8)"), ("all", "전체(기존 20 + hard 20)")):
            for n in dict.fromkeys(["cand_000", best]):
                L.append(_row(f"{title} · {n}", _j(fd / n / f"scores-{label}.json")).replace(f"| {title} · {n} |", f"| {title} | {n} |", 1))
        if best and best != "cand_000":
            L += ["", f"## 5. 최종 후보({best})가 바꾼 것", "", "### 제안자가 쓴 이유(rationale.md)", ""]
            L += [(rd / best / "rationale.md").read_text(encoding="utf-8") if (rd / best / "rationale.md").exists() else "(없음)"]
            src_old = (rd / "cand_000" / "harness.py").read_text(encoding="utf-8").splitlines()
            src_new = (rd / best / "harness.py").read_text(encoding="utf-8").splitlines()
            diff = "\n".join(difflib.unified_diff(src_old, src_new, "cand_000/harness.py", f"{best}/harness.py", lineterm=""))
            L += ["", "### 코드 diff (기준 대비)", "", "```diff", diff, "```"]
    out = HERE / f"report-{run}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys

    print(write_report(sys.argv[1] if len(sys.argv) > 1 else "r1"))
