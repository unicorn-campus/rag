"""별도 실험 1: 조(條) 단위 추출과 역량질문 범위 판별의 효과 비교.

원래 프로그램(graphrag/schema/)은 고치지 않고 함수와 입력 파일만 읽어 씀. 출력은 이 폴더에만 씀.

채우기 시험 청크 10건을 조문 단위로 나눠 조문마다 따로 추출하고, 같은 실행 결과를 세 가지로 채점함.
- 기준: 5회차(최종 스키마, 청크 단위, 3회 반복)의 저장된 추출 결과를 현재 기대 결과로 다시 채점(LLM 호출 없음)
- A 조 단위: 청크 안의 모든 조문을 따로 추출해 청크별로 합침
- B 조 단위 + 범위 판별: A에서 역량질문 대표 조문(competency-questions.md 표의 '답이 있는 대표 조문')만 남김
  (범위 판별은 조문 번호 규칙이라 LLM 호출 없음. B는 A와 같은 추출 결과에서 범위 밖 조문을 뺀 것)
- 참고 B+: B에 처리 기관 조문(제132조의16 특허심판원)을 더한 범위
- C 청크 단위 + 청크 범위 판별: 기준의 저장 결과에서 범위 안 조문이 하나도 없는 청크를 추출하지 않은 것으로 봄(LLM 호출 없음)

스키마·추출 프롬프트·채점 규칙은 5회차와 같음(schema-draft-r2.confirmed.json, 라이브러리 기본 프롬프트, fill-test-expected.json).

출력: results/article-test.json(원자료), article-test-report.md

실행(저장소 루트에서):
    python graphrag/experiments/scope_filter/article_test.py --repeat 3
    python graphrag/experiments/scope_filter/article_test.py --rescore    # LLM 호출 없이 저장된 결과로 다시 채점
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent                   # rag/graphrag/experiments/scope_filter
sys.path.insert(0, str(EXP_DIR.parents[1] / "schema"))      # 원래 프로그램의 함수를 읽기 전용으로 가져다 씀

from neo4j_graphrag.components.entity_relation_extractor import LLMEntityRelationExtractor
from neo4j_graphrag.components.graph_pruning import GraphPruning
from neo4j_graphrag.components.schema import GraphSchema
from neo4j_graphrag.components.types import Neo4jGraph

from common import (  # noqa: E402
    CQ_FILE,
    EXPECTED_FILE,
    MODEL_NAME,
    WORK_DIR,
    load_chunks,
    load_json_confirmed,
    make_llm,
    precheck,
    require_confirmed,
    setup_console,
    write_json,
)
from step7_fill_test import aggregate_runs, extract_chunk, rebuild_graph, score_run  # noqa: E402

SCHEMA_FILE = "schema-draft-r2.confirmed.json"   # 5회차·최종 스키마와 같은 초안(graphrag/schema/work/, 읽기만 함)
BASELINE_ROUND = 5                               # graphrag/schema/work/round-5.json(읽기만 함)
RAW_FILE = EXP_DIR / "results" / "article-test.json"
REPORT = EXP_DIR / "article-test-report.md"
CONCURRENCY = 4
EXTRA_SCOPE = ["제132조의16"]                     # 참고 B+: CQ04의 처리 기관(특허심판원)을 정한 조문

# 줄 맨 앞의 조문 머리: "제29조(특허요건)" 또는 "제31조 삭제"
_ARTICLE = re.compile(r"(?m)^(제\s*\d+\s*조(?:의\s*\d+)?)(?=\s*\(|\s+삭제)")
_CHAPTER_LINE = re.compile(r"(?m)^제\s*\d+\s*장(?:의\s*\d+)?\s+\S[^\n]*\n?")
_ARTICLE_REF = re.compile(r"제\s*\d+\s*조(?:의\s*\d+)?")


def norm_article(a: str) -> str:
    return re.sub(r"\s+", "", a)


# ---------------------------------------------------------------------------
# 조 단위 나누기와 범위 판별
# ---------------------------------------------------------------------------
def split_articles(chunk_index: int, chunks: dict[int, str]) -> list[dict]:
    """청크 본문을 조문 머리 기준으로 나눔.

    청크 첫 머리 앞의 조각은 앞 청크의 마지막 조문이 이어진 것으로 봄. 장 제목 줄과 '삭제'만 있는 조문은 뺌.
    """
    text = _CHAPTER_LINE.sub("", chunks[chunk_index])
    heads = list(_ARTICLE.finditer(text))
    units = []
    lead = text[: heads[0].start()] if heads else text
    if lead.strip():
        prev = list(_ARTICLE.finditer(chunks.get(chunk_index - 1, "")))
        units.append({"article": norm_article(prev[-1].group(1)) if prev else "알 수 없음", "text": lead.strip(),
                      "continued": True})
    for i, h in enumerate(heads):
        body = text[h.start(): heads[i + 1].start() if i + 1 < len(heads) else len(text)].strip()
        if re.fullmatch(r"제\s*\d+\s*조(?:의\s*\d+)?\s+삭제\s*(<[^>]*>)?", body):
            continue
        units.append({"article": norm_article(h.group(1)), "text": body, "continued": False})
    return units


def cq_scope() -> list[str]:
    """확정된 역량질문 표의 '답이 있는 대표 조문' 열에서 조문 번호를 모음."""
    text = require_confirmed(CQ_FILE, "역량질문")
    rows = re.findall(r"^\|\s*CQ\d+\s*\|[^|]*\|[^|]*\|\s*([^|]+?)\s*\|", text, flags=re.M)
    return sorted({norm_article(a) for cell in rows for a in _ARTICLE_REF.findall(cell)})


# ---------------------------------------------------------------------------
# 추출(A: 조문마다 따로) → 청크별 합치기
# ---------------------------------------------------------------------------
async def extract_articles(schema: GraphSchema, units: list[dict]) -> list[dict]:
    """조문마다 추출하고 GraphPruning으로 정리함. 조문별 결과(노드·관계 목록)를 돌려줌."""
    extractor = LLMEntityRelationExtractor(llm=make_llm(reasoning_effort="medium"), create_lexical_graph=False)
    pruner = GraphPruning()
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(u: dict) -> dict:
        async with sem:
            graph = await extract_chunk(extractor, schema, {"text": u["text"], "chunk_index": u["chunk_index"]})
        pruned = await pruner.run(graph=graph, schema=schema)
        return {**{k: u[k] for k in ("chunk_index", "article", "continued")}, "graph": pruned.graph.model_dump()}

    return await asyncio.gather(*(one(u) for u in units))


def chunk_graphs(expected: dict, article_results: list[dict], scope: set[str] | None) -> list[Neo4jGraph]:
    """조문별 결과를 청크별 그래프로 합침. scope가 있으면 범위 안 조문만 씀."""
    graphs = []
    for c in expected["chunks"]:
        nodes, rels = [], []
        for r in article_results:
            if r["chunk_index"] != c["chunk_index"] or (scope is not None and r["article"] not in scope):
                continue
            g = Neo4jGraph.model_validate(r["graph"])
            nodes += g.nodes
            rels += g.relationships
        graphs.append(Neo4jGraph(nodes=nodes, relationships=rels))
    return graphs


def score(draft: dict, expected: dict, graphs: list[Neo4jGraph]) -> dict:
    return score_run(draft, expected, graphs, [""] * len(graphs), Counter(), Counter())


def baseline(draft: dict, expected: dict, keep_chunks: set[int] | None = None) -> dict:
    """5회차 저장 결과(청크 단위, 3회)를 현재 기대 결과로 다시 채점함.

    keep_chunks가 있으면 그 밖의 청크는 추출하지 않은 것(빈 그래프)으로 봄(청크 단위 범위 판별).
    """
    prev = json.loads((WORK_DIR / f"round-{BASELINE_ROUND}.json").read_text(encoding="utf-8"))
    runs = []
    for run in prev["runs"]:
        stored = {c["chunk_index"]: c for c in run["chunks"]}
        graphs = [rebuild_graph(stored[c["chunk_index"]]["graph"])
                  if keep_chunks is None or c["chunk_index"] in keep_chunks else Neo4jGraph()
                  for c in expected["chunks"]]
        runs.append(score(draft, expected, graphs))
    return aggregate_runs(runs)


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------
def render(raw: dict, results: dict[str, dict]) -> str:
    names = {"base": "기준(청크 단위, 5회차)", "C": "C 청크 단위 + 청크 범위 판별", "A": "A 조 단위", "B": "B 조 단위 + 범위 판별", "B+": "참고 B+ (B + 제132조의16)"}
    lines = [
        "# 조 단위 추출·범위 판별 실험 보고서",
        "",
        f"- 실행: {raw['generated_at']}, 추출 LLM `{MODEL_NAME}`(라이브러리 기본 추출 프롬프트), 스키마 `{SCHEMA_FILE}`(최종 스키마와 같음), {raw['repeat']}회 반복 평균",
        "- 목적: 지어냄의 원인이 스키마가 아니라 추출 단위(조문이 섞인 청크)와 범위(역량질문과 무관한 조문)에 있는지 확인",
        "- 조 단위(article unit): 청크 본문을 줄 맨 앞 조문 머리(`제N조(…)`)로 나눠 조문마다 따로 추출하고 청크별로 합쳐 채점. 청크 첫 머리 앞 조각은 앞 청크 마지막 조문의 이어짐으로 봄",
        f"- 범위 판별(scope filter): 역량질문 표의 '답이 있는 대표 조문'({', '.join(raw['scope'])})만 추출 대상으로 남김. 조문 번호 규칙이라 LLM 호출 없음",
        "- 기준: 5회차 저장 결과를 현재 기대 결과(`fill-test-expected.json`)로 다시 채점(LLM 호출 없음). C는 같은 저장 결과에서 범위 밖 청크만 뺌. A·B·B+는 같은 조 단위 추출 결과를 범위만 달리해 채점",
        "- 지표: 재현율(기대 항목 중 뽑힌 비율), 지어냄 비율(뽑힌 항목 중 기대에 없는 비율), 역량질문 답 가능(3회 중 2회 이상 패턴 경로가 이어짐)",
        "",
        "## 1. 비교",
        "",
        "| 방식 | 재현율(범위) | 지어냄 비율(범위) | 뽑힌 항목(평균) | 역량질문 답 가능 |",
        "|---|---|---|---|---|",
    ]
    for key, r in results.items():
        m = r["metrics"]
        lines.append(f"| {names[key]} | {m['recall']:.3f} ({m['recall_range'][0]:.3f}~{m['recall_range'][1]:.3f}) | "
                     f"{m['hallucination_rate']:.3f} ({m['hallucination_range'][0]:.3f}~{m['hallucination_range'][1]:.3f}) | "
                     f"{m['extracted']:.1f} | {m['cq_answerable']}/{m['cq_total']} |")
    lines += ["", "## 2. 청크별 지어냄(뽑힌 항목 중 기대에 없는 항목, 3회 평균)", "",
              "| 청크 | 구분 | 조문 | 기대 | " + " | ".join(names[k] for k in results) + " |",
              "|---|---|---|---|" + "---|" * len(results)]
    arts = {}
    for u in raw["units"]:
        arts.setdefault(u["chunk_index"], []).append(u["article"] + ("(이어짐)" if u["continued"] else ""))
    for j, c in enumerate(results["base"]["chunks"]):
        cells = [f"{results[k]['chunks'][j]['extra']:.1f} / 놓침 {c['expected'] - results[k]['chunks'][j]['hit']:.1f}" for k in results]
        lines.append(f"| {c['chunk_index']} | {c['kind']} | {', '.join(arts.get(c['chunk_index'], []))} | {c['expected']} | " + " | ".join(cells) + " |")
    lines += ["", "- 칸 형식: `지어냄 평균 / 놓침 평균`", ""]
    lines += ["## 3. 역량질문별 답 가능(3회 중 이어진 횟수)", "",
              "| 역량질문 | " + " | ".join(names[k] for k in results) + " |", "|---|" + "---|" * len(results)]
    for i, q in enumerate(results["base"]["cq"]):
        lines.append(f"| {q['cq_id']} | " + " | ".join(results[k]["cq"][i]["runs_ok"] for k in results) + " |")
    scope = set(raw["scope"])
    empty = Counter()
    for run in raw["runs"]:
        for a in run:
            if not a["graph"]["nodes"]:
                empty[(a["chunk_index"], a["article"])] += 1
    lines += ["", "## 4. 빈 결과 조문(정리 후 노드 0개, 반복 중 횟수)", "",
              "- 조문 하나만 넣으면 LLM이 `{\"nodes\": [], \"relationships\": []}`를 그대로 돌려주는 경우가 있음(가지치기로 지워진 것이 아님)", "",
              "| 청크 | 조문 | 범위 | 빈 결과 |", "|---|---|---|---|"]
    for u in raw["units"]:
        lines.append(f"| {u['chunk_index']} | {u['article']} | {'안' if u['article'] in scope else '밖'} | "
                     f"{empty[(u['chunk_index'], u['article'])]}/{raw['repeat']} |")
    for key in ("A", "B"):
        lines += ["", f"## 5-{key}. {names[key]}: 자주 남은 지어냄(3회 중 2회 이상)", ""]
        for c in results[key]["chunks"]:
            frequent = [x for x in c["extra_nodes"] + c["extra_rels"] if int(x.rsplit("(", 1)[1].split("/")[0]) >= 2]
            if frequent:
                lines.append(f"- chunk {c['chunk_index']}: " + "; ".join(frequent))
        lines += ["", f"### 자주 놓친 항목(3회 중 2회 이상)", ""]
        for c in results[key]["chunks"]:
            frequent = [x for x in c["missed_nodes"] + c["missed_rels"] if int(x.rsplit("(", 1)[1].split("/")[0]) >= 2]
            if frequent:
                lines.append(f"- chunk {c['chunk_index']}: " + "; ".join(frequent))
    return "\n".join(lines) + "\n"


async def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--rescore", action="store_true", help="LLM 호출 없이 work/article-test.json으로 다시 채점")
    args = parser.parse_args()
    precheck(need_llm=not args.rescore)

    draft = load_json_confirmed(WORK_DIR / SCHEMA_FILE, "스키마 초안")
    expected = load_json_confirmed(EXPECTED_FILE, "기대 추출 결과")
    schema = GraphSchema.model_validate(draft["schema"])
    scope = cq_scope()

    if args.rescore:
        raw = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    else:
        chunks = {c["chunk_index"]: c["text"] for c in load_chunks()}
        units = [{"chunk_index": c["chunk_index"], **u} for c in expected["chunks"]
                 for u in split_articles(c["chunk_index"], chunks)]
        print(f"[조 단위] 시험 청크 {len(expected['chunks'])}건 → 조문 {len(units)}개, 범위 안 조문 "
              f"{sum(u['article'] in scope for u in units)}개 (범위: {', '.join(scope)})")
        runs = []
        for i in range(args.repeat):
            runs.append(await extract_articles(schema, units))
            print(f"  추출 {i + 1}/{args.repeat} 완료(조문 {len(units)}개)")
        raw = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "repeat": args.repeat, "scope": scope,
               "units": [{k: u[k] for k in ("chunk_index", "article", "continued")} for u in units], "runs": runs}
        write_json(RAW_FILE, raw)

    variants = {"A": None, "B": set(raw["scope"]), "B+": set(raw["scope"]) | set(EXTRA_SCOPE)}
    in_scope_chunks = {u["chunk_index"] for u in raw["units"] if u["article"] in set(raw["scope"])}
    results = {"base": baseline(draft, expected), "C": baseline(draft, expected, in_scope_chunks)}
    for key, sc in variants.items():
        results[key] = aggregate_runs([score(draft, expected, chunk_graphs(expected, run, sc)) for run in raw["runs"]])
    REPORT.write_text(render(raw, results), encoding="utf-8")
    for key, r in results.items():
        m = r["metrics"]
        print(f"[{key}] 재현율 {m['recall']:.3f}, 지어냄 비율 {m['hallucination_rate']:.3f}, 역량질문 {m['cq_answerable']}/{m['cq_total']}")
    print(f"[보고서] {REPORT.name}")


if __name__ == "__main__":
    asyncio.run(main())
