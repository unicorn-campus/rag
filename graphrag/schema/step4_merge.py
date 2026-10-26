"""4단계: 스키마 후보 합치기.

candidates/sample-*.json의 LLM 원본 응답(llm_raw)에서 노드 타입, 관계 타입, 패턴별로
등장한 표본 수를 셈. 원본을 쓰는 이유: neo4j-graphrag의 V1 필터가 속성 없는 노드 타입을 지워
후보가 줄어들기 때문(이 단계는 후보를 지우지 않음).

- 이름이 다르고 뜻이 같아 보이는 후보는 합치지 않고 "합칠 후보"로 표시만 함(LLM이 묶음 제안)
- 등장 수가 적은 후보도 지우지 않음

출력: candidates/merged.md(사람용), candidates/merged.json(다음 단계 입력)

실행:
    python step4_merge.py
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict

from common import (
    CANDIDATES_DIR,
    MERGED_JSON,
    MERGED_MD,
    ask_json,
    make_llm,
    precheck,
    setup_console,
    write_json,
)

SIMILAR_PROMPT = """아래는 특허법 문서에서 뽑은 그래프 스키마 후보 이름 목록임.
이름은 다르지만 뜻이 같거나 거의 같아 보이는 것끼리 묶어 "합칠 후보" 그룹을 제안함.
- 그룹은 2개 이상 이름으로 구성. 확실히 뜻이 다른 것은 묶지 않음
- 묶기만 하고 대표 이름은 정하지 않음. 이유는 한국어 한 문장

JSON 객체 하나만 출력:
{{"node_groups": [{{"labels": ["A", "B"], "reason": "..."}}], "relationship_groups": [{{"labels": ["X", "Y"], "reason": "..."}}]}}

<노드타입>
{nodes}
</노드타입>
<관계타입>
{rels}
</관계타입>
"""


def load_raw() -> dict[int, dict]:
    out = {}
    for p in sorted(CANDIDATES_DIR.glob("sample-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        raw = d["llm_raw"]
        if isinstance(raw, dict):
            out[d["chunk_index"]] = raw
    return out


def count(raws: dict[int, dict]) -> dict:
    nodes: dict[str, dict] = defaultdict(lambda: {"samples": set(), "properties": defaultdict(set)})
    rels: dict[str, dict] = defaultdict(lambda: {"samples": set(), "properties": defaultdict(set)})
    pats: dict[tuple, set] = defaultdict(set)
    for idx, raw in raws.items():
        for n in raw.get("node_types", []):
            label = n["label"] if isinstance(n, dict) else str(n)
            nodes[label]["samples"].add(idx)
            for p in (n.get("properties") or []) if isinstance(n, dict) else []:
                nodes[label]["properties"][f"{p.get('name')}:{p.get('type', 'STRING')}"].add(idx)
        for r in raw.get("relationship_types", []):
            label = r["label"] if isinstance(r, dict) else str(r)
            rels[label]["samples"].add(idx)
            for p in (r.get("properties") or []) if isinstance(r, dict) else []:
                rels[label]["properties"][f"{p.get('name')}:{p.get('type', 'STRING')}"].add(idx)
        for p in raw.get("patterns", []):
            key = (p["source"], p["relationship"], p["target"]) if isinstance(p, dict) else tuple(p)
            pats[key].add(idx)

    def pack(d: dict) -> list[dict]:
        return sorted(
            [
                {
                    "label": k,
                    "count": len(v["samples"]),
                    "samples": sorted(v["samples"]),
                    "properties": {pk: len(pv) for pk, pv in sorted(v["properties"].items(), key=lambda x: -len(x[1]))},
                }
                for k, v in d.items()
            ],
            key=lambda x: (-x["count"], x["label"]),
        )

    return {
        "node_types": pack(nodes),
        "relationship_types": pack(rels),
        "patterns": sorted(
            [{"source": s, "relationship": r, "target": t, "count": len(v), "samples": sorted(v)} for (s, r, t), v in pats.items()],
            key=lambda x: (-x["count"], x["source"], x["relationship"]),
        ),
    }


def render_md(m: dict, n_samples: int) -> str:
    def props(d: dict) -> str:
        return ", ".join(f"{k}({v})" for k, v in d.items()) or "-"

    def samples(s: list[int]) -> str:
        return ", ".join(map(str, s))

    lines = [
        "# 스키마 후보 합치기 결과",
        "",
        f"- 표본 수: {n_samples}건(`SchemaFromTextExtractor` 호출별 원본 응답 기준)",
        "- 등장 수: 그 후보가 나온 표본 청크 수. 속성 옆 괄호도 같은 뜻",
        "- 노드 타입(node type): 그래프의 점(개체) 종류. 관계 타입(relationship type): 점을 잇는 선의 종류",
        "- 패턴(pattern): `시작 노드 타입 -관계 타입-> 끝 노드 타입` 3개 묶음. 어떤 관계가 어떤 노드 사이에 오는지 정함",
        "- 이 단계에서는 등장 수가 적은 후보도 지우지 않고, 뜻이 같아 보이는 후보도 합치지 않고 표시만 함",
        "",
        f"## 1. 노드 타입 후보 ({len(m['node_types'])}개)",
        "",
        "| 노드 타입 | 등장 수 | 속성(등장 수) | 등장한 표본 번호 |",
        "|---|---|---|---|",
    ]
    lines += [f"| {n['label']} | {n['count']} | {props(n['properties'])} | {samples(n['samples'])} |" for n in m["node_types"]]
    lines += ["", f"## 2. 관계 타입 후보 ({len(m['relationship_types'])}개)", "",
              "| 관계 타입 | 등장 수 | 속성(등장 수) | 등장한 표본 번호 |", "|---|---|---|---|"]
    lines += [f"| {r['label']} | {r['count']} | {props(r['properties'])} | {samples(r['samples'])} |" for r in m["relationship_types"]]
    lines += ["", f"## 3. 패턴 후보 ({len(m['patterns'])}개)", "",
              "| 시작 | 관계 | 끝 | 등장 수 | 등장한 표본 번호 |", "|---|---|---|---|---|"]
    lines += [f"| {p['source']} | {p['relationship']} | {p['target']} | {p['count']} | {samples(p['samples'])} |" for p in m["patterns"]]
    lines += ["", "## 4. 합칠 후보(표시만 함)", "", "### 4.1 노드 타입", "", "| 묶음 | 이름 | 이유 |", "|---|---|---|"]
    lines += [f"| N{i} | {', '.join(g['labels'])} | {g['reason']} |" for i, g in enumerate(m["merge_candidates"]["node_groups"], 1)]
    lines += ["", "### 4.2 관계 타입", "", "| 묶음 | 이름 | 이유 |", "|---|---|---|"]
    lines += [f"| R{i} | {', '.join(g['labels'])} | {g['reason']} |" for i, g in enumerate(m["merge_candidates"]["relationship_groups"], 1)]
    return "\n".join(lines) + "\n"


async def main() -> None:
    setup_console()
    precheck()
    raws = load_raw()
    merged = count(raws)

    llm = make_llm(reasoning_effort="medium")
    groups = await ask_json(llm, SIMILAR_PROMPT.format(
        nodes="\n".join(n["label"] for n in merged["node_types"]),
        rels="\n".join(r["label"] for r in merged["relationship_types"]),
    ))
    known_n = {n["label"] for n in merged["node_types"]}
    known_r = {r["label"] for r in merged["relationship_types"]}
    merged["merge_candidates"] = {
        "node_groups": [g for g in groups.get("node_groups", []) if len(set(g["labels"]) & known_n) >= 2],
        "relationship_groups": [g for g in groups.get("relationship_groups", []) if len(set(g["labels"]) & known_r) >= 2],
    }
    merged["n_samples"] = len(raws)

    write_json(MERGED_JSON, merged)
    MERGED_MD.write_text(render_md(merged, len(raws)), encoding="utf-8")
    print(
        f"[완료] 노드 타입 {len(merged['node_types'])}, 관계 타입 {len(merged['relationship_types'])}, "
        f"패턴 {len(merged['patterns'])}, 합칠 후보 노드 {len(merged['merge_candidates']['node_groups'])}묶음 / "
        f"관계 {len(merged['merge_candidates']['relationship_groups'])}묶음 → {MERGED_MD}"
    )


if __name__ == "__main__":
    asyncio.run(main())
