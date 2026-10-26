"""라운드별 결과(results/*-dev.json, *-holdout.json)를 한 표로 모아 results-table.md에 씀(LLM 호출 없음).

실행(저장소 루트에서):
    python graphrag/experiments/agentic/summarize.py
"""

from __future__ import annotations

import json
import re

from harness import EXP_DIR, RESULTS_DIR

ORDER = ["r1", "r2", "r3", "ho"]


def main() -> None:
    rows = []
    for p in sorted(RESULTS_DIR.glob("*-*.json")):
        m = re.fullmatch(r"(.+)-(r\d|ho)(\+iso)?-(dev|holdout)", p.stem)
        if not m:
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        mt = d["metrics"]
        rows.append({"round": m.group(2), "name": m.group(1) + (m.group(3) or ""), "split": m.group(4),
                     "recall": mt["recall"], "rmin": mt["recall_range"][0], "rmax": mt["recall_range"][1],
                     "hal": mt["hallucination_rate"], "hmin": mt["hallucination_range"][0], "hmax": mt["hallucination_range"][1],
                     "cq": f"{mt['cq_answerable']}/{mt['cq_total']}", "cq04": d["cq"][3]["runs_ok"],
                     "calls": d["llm_calls_per_chunk"], "repeat": d["repeat"]})
    rows.sort(key=lambda r: (ORDER.index(r["round"]), r["split"], r["name"]))
    lines = ["# 라운드별 결과 표", "", "- 재현율·지어냄은 반복 평균(최소~최대). `+iso`는 저장 결과에 고립 노드 제거를 얹어 LLM 없이 다시 채점한 것",
             "", "| 라운드 | 자료 | 방안 | 반복 | 재현율 | 지어냄 비율 | 역량질문 | CQ04 | 청크당 LLM |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['round']} | {r['split']} | {r['name']} | {r['repeat']} | {r['recall']:.3f} ({r['rmin']:.3f}~{r['rmax']:.3f}) | "
                     f"{r['hal']:.3f} ({r['hmin']:.3f}~{r['hmax']:.3f}) | {r['cq']} | {r['cq04']} | {r['calls']:.1f} |")
    (EXP_DIR / "results-table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
