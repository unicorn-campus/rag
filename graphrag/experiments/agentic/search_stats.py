"""검색 비교 결과의 통계(LLM 호출 없음): 방식별 평균, 회차별 평균, 짝지은 차이의 부트스트랩 95% 구간, 문항별 승패.

- 문항 단위 부트스트랩: 문항을 복원 추출해 "방식 A − 방식 B"의 문항 평균 차를 1만 번 다시 계산
  (문항마다 5회 평균을 먼저 낸 뒤 비교. 구간이 0을 포함하지 않으면 차이가 있다고 봄)
- 회차 단위 승패: 같은 회차의 방식별 요점 재현율 평균을 비교

실행(저장소 루트에서):
    python graphrag/experiments/agentic/search_stats.py --tag react2-new
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
from collections import defaultdict

from harness import EXP_DIR, RESULTS_DIR

NAMES = {"base": "원래 검색", "agentic": "고정 워크플로우", "react": "ReAct v1", "react2": "ReAct v2(원문 포함)",
         "react3": "ReAct v3(거절 장치)"}
GRAPH_WEAK = {"N02", "N18"}   # 특허법에는 있지만 그래프 스키마 밖 조문(특허료, 등록지연 연장)이라 원문까지 가지 못하는 질문
N_BOOT = 10000


def load(tag: str) -> list[dict]:
    return json.loads((RESULTS_DIR / f"search-eval-{tag}.json").read_text(encoding="utf-8"))


def per_question(rows: list[dict]) -> dict[str, dict[str, float]]:
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["keypoint_recall"] is not None:
            acc[r["search"]][r["id"]].append(r["keypoint_recall"])
    return {m: {q: sum(v) / len(v) for q, v in qs.items()} for m, qs in acc.items()}


def per_run(rows: list[dict]) -> dict[str, dict[int, float]]:
    acc: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["keypoint_recall"] is not None:
            acc[r["search"]][r["repeat"]].append(r["keypoint_recall"])
    return {m: {k: sum(v) / len(v) for k, v in sorted(reps.items())} for m, reps in acc.items()}


def bootstrap(a: dict[str, float], b: dict[str, float], seed: int = 7) -> tuple[float, float, float]:
    qs = sorted(set(a) & set(b))
    diffs = [a[q] - b[q] for q in qs]
    rng = random.Random(seed)
    boots = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(N_BOOT))
    return sum(diffs) / len(diffs), boots[int(0.025 * N_BOOT)], boots[int(0.975 * N_BOOT)]


def refusal_section(rows: list[dict]) -> list[str]:
    """거절 지표: 그래프 밖 질문은 거절률(높을수록 좋음), 답할 수 있는 질문은 잘못된 거절 수(낮을수록 좋음)."""
    if not any("refused" in r for r in rows):
        return []
    modes = [m for m in NAMES if any(r["search"] == m for r in rows)]
    outside = [r for r in rows if r.get("expected") == "outside"]
    weak = [r for r in rows if r["id"] in GRAPH_WEAK]
    answerable = [r for r in rows if r.get("expected") != "outside" and r["id"] not in GRAPH_WEAK]

    def rate(rs: list[dict], m: str) -> str:
        xs = [r["refused"] for r in rs if r["search"] == m and r.get("refused") is not None]
        return f"{sum(xs)}/{len(xs)} ({sum(xs) / len(xs):.2f})" if xs else "-"

    lines = ["", "## 4. 거절", "", "| 방식 | 그래프 밖 질문 거절(높을수록 좋음) | 그래프가 약한 N02·N18 거절 | 답할 수 있는 질문의 잘못된 거절(낮을수록 좋음) |",
             "|---|---|---|---|"]
    for m in modes:
        lines.append(f"| {NAMES[m]} | {rate(outside, m)} | {rate(weak, m)} | {rate(answerable, m)} |")
    if outside:
        lines += ["", "| 그래프 밖 문항 | " + " | ".join(NAMES[m] for m in modes) + " |", "|---|" + "---|" * len(modes)]
        for q in sorted({r["id"] for r in outside}):
            lines.append(f"| {q} | " + " | ".join(rate([r for r in outside if r["id"] == q], m) for m in modes) + " |")
    return lines


def render(rows: list[dict], tag: str) -> str:
    if not any(r["keypoint_recall"] is not None for r in rows):   # 그래프 밖 질문만 있는 자료(요점 없음)
        return "\n".join([f"# 검색 비교 통계({tag})", "", f"- 자료: results/search-eval-{tag}.json, 질문 {len({r['id'] for r in rows})}개(요점 없음, 거절 여부만 봄)"]
                         + refusal_section(rows)) + "\n"
    pq, pr = per_question(rows), per_run(rows)
    modes = [m for m in NAMES if m in pq]
    by_type: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    qtype = {r["id"]: r.get("expected", "") for r in rows}
    for m in modes:
        for q, v in pq[m].items():
            by_type[m][qtype[q]].append(v)
    lines = [f"# 검색 비교 통계({tag})", "", f"- 자료: results/search-eval-{tag}.json, 채점 문항 {len(pq[modes[0]])}개",
             "", "## 1. 방식별 요점 재현율", "", "| 방식 | 평균 | 회차별 | " + " | ".join(sorted({t for t in qtype.values()})) + " |",
             "|---|---|---|" + "---|" * len({t for t in qtype.values()})]
    for m in modes:
        runs = ", ".join(f"{v:.3f}" for v in pr[m].values())
        types = " | ".join(f"{sum(by_type[m][t]) / len(by_type[m][t]):.3f}" if by_type[m][t] else "-" for t in sorted({t for t in qtype.values()}))
        lines.append(f"| {NAMES[m]} | {sum(pq[m].values()) / len(pq[m]):.3f} | {runs} | {types} |")
    lines += ["", "## 2. 짝지은 비교(문항 단위 부트스트랩 95% 구간)", "",
              "| 비교 | 평균 차 | 95% 구간 | 문항 승/패/무 | 회차 승/패 |", "|---|---|---|---|---|"]
    for a, b in itertools.combinations(modes, 2):
        d, lo, hi = bootstrap(pq[a], pq[b])
        qs = set(pq[a]) & set(pq[b])
        w = sum(pq[a][q] > pq[b][q] + 1e-9 for q in qs)
        l = sum(pq[b][q] > pq[a][q] + 1e-9 for q in qs)
        rw = sum(pr[a][k] > pr[b][k] for k in pr[a] if k in pr[b])
        rl = sum(pr[a][k] < pr[b][k] for k in pr[a] if k in pr[b])
        sig = " (0 미포함)" if lo > 0 or hi < 0 else ""
        lines.append(f"| {NAMES[a]} − {NAMES[b]} | {d:+.3f} | {lo:+.3f} ~ {hi:+.3f}{sig} | {w}/{l}/{len(qs) - w - l} | {rw}/{rl} |")
    lines += ["", "## 3. 문항별 요점 재현율(5회 평균)", "", "| 문항 | 유형 | " + " | ".join(NAMES[m] for m in modes) + " |",
              "|---|---|" + "---|" * len(modes)]
    for q in sorted(pq[modes[0]]):
        lines.append(f"| {q} | {qtype[q]} | " + " | ".join(f"{pq[m].get(q, float('nan')):.2f}" for m in modes) + " |")
    lines += refusal_section(rows)
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True)
    args = p.parse_args()
    text = render(load(args.tag), args.tag)
    (EXP_DIR / f"search-stats-{args.tag}.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
