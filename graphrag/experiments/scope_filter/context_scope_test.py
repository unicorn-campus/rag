"""별도 실험 2: 청크 문맥 추출 + 범위 안 조문 항목만 남기기.

실험 1(article_test.py)에서 조문을 따로 떼어 추출하면 문맥이 사라져 재현율이 떨어졌음.
여기서는 추출은 기준과 같이 청크 전체로 하고(문맥 유지), 뽑힌 항목 중 범위 안 조문에서 나온 것만 남김.

- 항목의 출처 판별(규칙, LLM 호출 없음). 관계는 양 끝 노드가 모두 남을 때만 남김
  - 통째 일치: 노드 이름(공백·문장부호·괄호 속 한자를 지운 형태)이 범위 안 조문 본문에 그대로 들어 있으면 남김
  - 단어 귀속: 이름의 단어가 가장 많이 겹치는 조문이 범위 안이면 남김
- 범위
  - D: 역량질문 표의 '답이 있는 대표 조문'(실험 1의 B와 같은 범위)
  - D+: D + 제33조(HOLDS: 특허를 받을 수 있는 자) + 제132조의16(특허심판원, CQ04 처리 기관)
- 추출 결과
  - 5회차: 원래 채우기 시험 5회차의 저장 결과(3회)를 그대로 거름 → 기준과 같은 추출로 짝지어 비교(LLM 호출 없음)
  - 새 실행: 같은 설정으로 새로 3회 추출해 거름(실행 변동 확인용)

원래 프로그램(graphrag/schema/)은 고치지 않고 함수와 입력 파일만 읽어 씀. 출력은 이 폴더에만 씀.
출력: results/context-scope.json(새 실행 원자료), context-scope-report.md

실행(저장소 루트에서):
    python graphrag/experiments/scope_filter/context_scope_test.py --repeat 3
    python graphrag/experiments/scope_filter/context_scope_test.py --rescore   # LLM 호출 없이 다시 채점
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime

from article_test import (
    EXP_DIR,
    SCHEMA_FILE,
    BASELINE_ROUND,
    cq_scope,
    norm_article,
    score,
    split_articles,
)
from common import EXPECTED_FILE, MODEL_NAME, WORK_DIR, load_chunks, load_json_confirmed, precheck, setup_console, write_json
from neo4j_graphrag.components.schema import GraphSchema
from neo4j_graphrag.components.types import Neo4jGraph
from step7_fill_test import aggregate_runs, extract_run, norm, rebuild_graph

RAW_FILE = EXP_DIR / "results" / "context-scope.json"
REPORT = EXP_DIR / "context-scope-report.md"
EXTRA_SCOPE = ["제33조", "제132조의16"]


# ---------------------------------------------------------------------------
# 범위 안 조문 항목만 남기기
# ---------------------------------------------------------------------------
_WORD = re.compile(r"[가-힣A-Za-z0-9]+")
_HANJA_PAREN = re.compile(r"\([一-鿿]+\)")


def words(text: str) -> set[str]:
    return set(_WORD.findall(_HANJA_PAREN.sub("", text)))


def chunk_articles(expected: dict, chunks: dict[int, str]) -> dict[int, list[dict]]:
    """청크별 조문(번호, 정규화 본문, 단어 집합)."""
    return {c["chunk_index"]: [{"article": u["article"], "norm": norm(u["text"]), "words": words(u["text"])}
                               for u in split_articles(c["chunk_index"], chunks)]
            for c in expected["chunks"]}


def in_scope(name: str, articles: list[dict], scope: set[str], mode: str) -> bool:
    """노드 이름이 범위 안 조문에서 나왔는지 판별.

    - substr: 이름(정규화)이 범위 안 조문 본문에 통째로 들어 있으면 남김
    - word: 이름의 단어가 가장 많이 겹치는 조문(동점이면 모두)에 범위 안 조문이 있으면 남김.
      이름 규칙상 괄호 속 부연을 빼고 이름을 만들어 본문과 글자가 그대로 겹치지 않는 경우를 살리기 위함
    """
    if mode == "substr":
        return any(norm(name) in a["norm"] for a in articles if a["article"] in scope)
    ws = words(name)
    if not ws or not articles:
        return False
    overlap = [len(ws & a["words"]) / len(ws) for a in articles]
    best = max(overlap)
    return best > 0 and any(a["article"] in scope for a, o in zip(articles, overlap) if o == best)


def filter_graph(graph: Neo4jGraph, articles: list[dict], scope: set[str], mode: str) -> Neo4jGraph:
    """범위 안 조문에서 나온 노드와, 양 끝이 모두 남은 관계만 남김."""
    keep = {n.id for n in graph.nodes if in_scope(str(n.properties.get("name", "")), articles, scope, mode)}
    return Neo4jGraph(nodes=[n for n in graph.nodes if n.id in keep],
                      relationships=[r for r in graph.relationships if r.start_node_id in keep and r.end_node_id in keep])


def score_runs(draft: dict, expected: dict, runs: list[list[Neo4jGraph]], articles: dict[int, list[dict]] | None = None,
               scope: set[str] | None = None, mode: str = "") -> dict:
    scored = []
    for graphs in runs:
        if scope is not None:
            graphs = [filter_graph(g, articles[c["chunk_index"]], scope, mode) for g, c in zip(graphs, expected["chunks"])]
        scored.append(score(draft, expected, graphs))
    return aggregate_runs(scored)


def round5_graphs(expected: dict) -> list[list[Neo4jGraph]]:
    prev = json.loads((WORK_DIR / f"round-{BASELINE_ROUND}.json").read_text(encoding="utf-8"))
    out = []
    for run in prev["runs"]:
        stored = {c["chunk_index"]: c for c in run["chunks"]}
        out.append([rebuild_graph(stored[c["chunk_index"]]["graph"]) for c in expected["chunks"]])
    return out


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------
NAMES = {
    "r5": "5회차 기준(거르지 않음)",
    "r5_D+s": "5회차 + D+ 범위·통째 일치",
    "r5_Dw": "5회차 + D 범위·단어 귀속",
    "r5_D+w": "5회차 + D+ 범위·단어 귀속",
    "new": "새 실행 기준(거르지 않음)",
    "new_D+s": "새 실행 + D+ 범위·통째 일치",
    "new_Dw": "새 실행 + D 범위·단어 귀속",
    "new_D+w": "새 실행 + D+ 범위·단어 귀속",
}


def render(raw: dict, results: dict[str, dict]) -> str:
    lines = [
        "# 청크 문맥 추출 + 범위 판별 실험 보고서",
        "",
        f"- 실행: {raw['generated_at']}, 추출 LLM `{MODEL_NAME}`(라이브러리 기본 추출 프롬프트), 스키마 `{SCHEMA_FILE}`(최종 스키마와 같음), 각 3회 반복 평균",
        "- 방식: 추출은 청크 전체로 하고(문맥 유지), 범위 안 조문에서 나온 노드와 양 끝이 모두 남은 관계만 남김(규칙, LLM 호출 없음)",
        "- 출처 판별 규칙: 통째 일치(노드 이름이 범위 안 조문 본문에 그대로 들어 있음) / 단어 귀속(이름의 단어가 가장 많이 겹치는 조문이 범위 안이면 남김. 이름 규칙상 괄호 속 부연을 빼서 본문과 글자가 그대로 겹치지 않는 경우를 살림)",
        f"- D 범위: {', '.join(raw['scope'])}",
        f"- D+ 범위: D + {', '.join(EXTRA_SCOPE)}",
        f"- 5회차: `graphrag/schema/work/round-{BASELINE_ROUND}.json`의 저장 결과를 현재 기대 결과로 채점(기준과 같은 추출로 짝지어 비교). 새 실행: 같은 설정으로 새로 3회 추출",
        "- 참고(실험 1, `article-test-report.md`): 조 단위 + 범위 판별(B) 재현율 0.635, 지어냄 비율 0.086, 역량질문 7/9",
        "",
        "## 1. 비교",
        "",
        "| 방식 | 재현율(범위) | 지어냄 비율(범위) | 뽑힌 항목(평균) | 역량질문 답 가능 |",
        "|---|---|---|---|---|",
    ]
    for key, r in results.items():
        m = r["metrics"]
        lines.append(f"| {NAMES[key]} | {m['recall']:.3f} ({m['recall_range'][0]:.3f}~{m['recall_range'][1]:.3f}) | "
                     f"{m['hallucination_rate']:.3f} ({m['hallucination_range'][0]:.3f}~{m['hallucination_range'][1]:.3f}) | "
                     f"{m['extracted']:.1f} | {m['cq_answerable']}/{m['cq_total']} |")
    keys = ["r5", "r5_D+w", "new", "new_D+w"]
    lines += ["", "## 2. 청크별 지어냄 / 놓침(3회 평균)", "",
              "| 청크 | 구분 | 기대 | " + " | ".join(NAMES[k] for k in keys) + " |", "|---|---|---|" + "---|" * len(keys)]
    for j, c in enumerate(results["r5"]["chunks"]):
        cells = [f"{results[k]['chunks'][j]['extra']:.1f} / {c['expected'] - results[k]['chunks'][j]['hit']:.1f}" for k in keys]
        lines.append(f"| {c['chunk_index']} | {c['kind']} | {c['expected']} | " + " | ".join(cells) + " |")
    lines += ["", "## 3. 역량질문별 답 가능(3회 중 이어진 횟수)", "",
              "| 역량질문 | " + " | ".join(NAMES[k] for k in results) + " |", "|---|" + "---|" * len(results)]
    for i, q in enumerate(results["r5"]["cq"]):
        lines.append(f"| {q['cq_id']} | " + " | ".join(results[k]["cq"][i]["runs_ok"] for k in results) + " |")
    for key in ("r5_D+w", "new_D+w"):
        for label, fields in (("자주 남은 지어냄", ("extra_nodes", "extra_rels")), ("자주 놓친 항목", ("missed_nodes", "missed_rels"))):
            lines += ["", f"## {NAMES[key]}: {label}(3회 중 2회 이상)", ""]
            for c in results[key]["chunks"]:
                frequent = [x for f in fields for x in c[f] if int(x.rsplit("(", 1)[1].split("/")[0]) >= 2]
                if frequent:
                    lines.append(f"- chunk {c['chunk_index']}: " + "; ".join(frequent))
    return "\n".join(lines) + "\n"


async def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--rescore", action="store_true", help="LLM 호출 없이 results/context-scope.json으로 다시 채점")
    args = parser.parse_args()
    precheck(need_llm=not args.rescore)

    draft = load_json_confirmed(WORK_DIR / SCHEMA_FILE, "스키마 초안")
    expected = load_json_confirmed(EXPECTED_FILE, "기대 추출 결과")
    schema = GraphSchema.model_validate(draft["schema"])
    scope = cq_scope()
    chunks = {c["chunk_index"]: c["text"] for c in load_chunks()}

    if args.rescore:
        raw = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    else:
        runs = []
        for i in range(args.repeat):
            graphs, *_ = await extract_run(schema, expected, None)
            runs.append([g.model_dump() for g in graphs])
            print(f"  새 추출 {i + 1}/{args.repeat} 완료(청크 {len(graphs)}건)")
        raw = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "scope": scope, "runs": runs}
        write_json(RAW_FILE, raw)

    new_runs = [[Neo4jGraph.model_validate(g) for g in run] for run in raw["runs"]]
    articles = chunk_articles(expected, chunks)
    d, dp = set(scope), set(scope) | {norm_article(a) for a in EXTRA_SCOPE}
    results = {}
    for src, runs in (("r5", round5_graphs(expected)), ("new", new_runs)):
        results[src] = score_runs(draft, expected, runs)
        results[f"{src}_D+s"] = score_runs(draft, expected, runs, articles, dp, "substr")
        results[f"{src}_Dw"] = score_runs(draft, expected, runs, articles, d, "word")
        results[f"{src}_D+w"] = score_runs(draft, expected, runs, articles, dp, "word")
    REPORT.write_text(render(raw, results), encoding="utf-8")
    for key, r in results.items():
        m = r["metrics"]
        print(f"[{key}] 재현율 {m['recall']:.3f}, 지어냄 비율 {m['hallucination_rate']:.3f}, 역량질문 {m['cq_answerable']}/{m['cq_total']}")
    print(f"[보고서] {REPORT.name}")


if __name__ == "__main__":
    asyncio.run(main())
