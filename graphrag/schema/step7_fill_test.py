"""7단계: 채우기 시험(fill test).

시험용 청크를 확정된 스키마 초안과 함께 `LLMEntityRelationExtractor`에 넣어 추출하고,
`GraphPruning.run(graph, schema)`로 정리한 뒤(스키마 밖 타입·관계·패턴 모두 불허)
확정된 기대 추출 결과와 비교함.

지표
- 재현율(recall): 기대 항목 중 뽑힌 항목 비율. 기준 0.8 이상
- 지어냄 비율(hallucination rate): 뽑힌 항목 중 기대에 없는 항목 비율(관계없는 청크 포함). 기준 0.1 이하
- 역량질문 답 가능: 모든 역량질문의 패턴 경로가 추출 결과에서 1건 이상 이어짐
--repeat N(2 이상): 같은 설정으로 N번 추출해 재현율·지어냄 비율은 평균, 역량질문은 과반 실행에서 이어지면 답 가능으로 판정
(추출 LLM이 temperature 0에서도 실행마다 결과가 크게 달라 단일 실행 판정이 불안정하기 때문)
항목 = 노드(타입, 이름) + 관계(시작, 관계 타입, 끝). 이름 비교 규칙은 fill-test-expected.json의 match_rule

출력: work/round-{N}.json(회차 원자료), fill-test-report.md(모든 회차 다시 집계)

실행:
    python step7_fill_test.py --round 1
    python step7_fill_test.py --round 5 --repeat 3 --schema-file work/schema-draft-r2.confirmed.json
                                                    # 같은 설정 3회 반복, 평균으로 판정
    python step7_fill_test.py --round 1 --rescore   # LLM 호출 없이 저장된 추출 결과로 다시 채점
    python step7_fill_test.py --round 4 --prompt-template work/extraction-prompt-r4.txt
                                                    # 보완한 추출 프롬프트(3회차부터 사용자 허용)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter

from neo4j_graphrag.components.entity_relation_extractor import LLMEntityRelationExtractor
from neo4j_graphrag.components.graph_pruning import GraphPruning, PruningReason
from neo4j_graphrag.components.schema import GraphSchema
from neo4j_graphrag.components.types import Neo4jGraph, TextChunk, TextChunks
from neo4j_graphrag.exceptions import LLMGenerationError

from common import (
    DRAFT_FILE,
    EXPECTED_FILE,
    MODEL_NAME,
    REPORT_FILE,
    SCHEMA_DIR,
    WORK_DIR,
    load_chunks,
    load_competency_questions,
    load_json_confirmed,
    make_llm,
    precheck,
    setup_console,
    write_json,
)

RECALL_MIN = 0.8
HALLUCINATION_MAX = 0.1
RETRIES = 3

_HANJA_PAREN = re.compile(r"\([一-鿿]+\)")
_PUNCT = re.compile(r"[\s\"'“”‘’「」『』()\[\]ㆍ·.,、:;]")


def norm(name: str) -> str:
    """이름 비교용 정규화: 괄호 속 한자, 공백, 문장부호 제거."""
    return _PUNCT.sub("", _HANJA_PAREN.sub("", str(name)))


# ---------------------------------------------------------------------------
# 추출
# ---------------------------------------------------------------------------
async def extract_chunk(extractor: LLMEntityRelationExtractor, schema: GraphSchema, chunk: dict) -> Neo4jGraph:
    """청크 하나를 따로 추출(청크별 결과를 구분하기 위함). LLM 형식 오류는 재시도."""
    last: Exception | None = None
    for _ in range(RETRIES):
        try:
            return await extractor.run(
                chunks=TextChunks(chunks=[TextChunk(text=chunk["text"], index=chunk["chunk_index"])]),
                schema=schema,
            )
        except LLMGenerationError as e:
            last = e
    raise RuntimeError(f"chunk {chunk['chunk_index']} 추출 실패: {last}")


def graph_items(graph: Neo4jGraph) -> tuple[set, set, list[dict]]:
    """그래프를 (타입, 정규화 이름) 노드 집합과 (시작, 관계, 끝) 관계 집합으로 바꿈."""
    by_id = {n.id: (n.label, norm(n.properties.get("name", ""))) for n in graph.nodes}
    raw = {n.id: (n.label, str(n.properties.get("name", ""))) for n in graph.nodes}
    nodes = set(by_id.values())
    rels, rel_view = set(), []
    for r in graph.relationships:
        if r.start_node_id in by_id and r.end_node_id in by_id:
            rels.add((by_id[r.start_node_id], r.type, by_id[r.end_node_id]))
            rel_view.append({"start": raw[r.start_node_id], "type": r.type, "end": raw[r.end_node_id]})
    return nodes, rels, rel_view


def expected_items(exp: dict) -> tuple[dict, set, dict]:
    """기대 노드(별칭 포함 → 대표 이름)와 기대 관계 집합."""
    alias = {}  # (type, norm(alias)) -> (type, norm(name))
    for n in exp["nodes"]:
        key = (n["type"], norm(n["name"]))
        alias[key] = key
        for a in n.get("aliases", []):
            alias.setdefault((n["type"], norm(a)), key)
    nodes = {(n["type"], norm(n["name"])) for n in exp["nodes"]}
    rels = {
        ((r["start"]["type"], norm(r["start"]["name"])), r["type"], (r["end"]["type"], norm(r["end"]["name"])))
        for r in exp["relationships"]
    }
    return alias, rels, {"nodes": nodes}


def score_chunk(graph: Neo4jGraph, exp: dict) -> dict:
    got_nodes, got_rels, rel_view = graph_items(graph)
    alias, exp_rels, e = expected_items(exp)
    exp_nodes = e["nodes"]
    canon = lambda k: alias.get(k, k)  # noqa: E731
    got_nodes_c = {canon(n) for n in got_nodes}
    got_rels_c = {(canon(s), t, canon(o)) for s, t, o in got_rels}

    hit_nodes = exp_nodes & got_nodes_c
    hit_rels = exp_rels & got_rels_c
    extra_nodes = got_nodes_c - exp_nodes
    extra_rels = got_rels_c - exp_rels
    return {
        "expected": len(exp_nodes) + len(exp_rels),
        "extracted": len(got_nodes_c) + len(got_rels_c),
        "hit": len(hit_nodes) + len(hit_rels),
        "extra": len(extra_nodes) + len(extra_rels),
        "missed_nodes": sorted(f"{t}:{n}" for t, n in exp_nodes - got_nodes_c),
        "missed_rels": sorted(f"{s[0]}:{s[1]} -{t}-> {o[0]}:{o[1]}" for s, t, o in exp_rels - got_rels_c),
        "extra_nodes": sorted(f"{t}:{n}" for t, n in extra_nodes),
        "extra_rels": sorted(f"{s[0]}:{s[1]} -{t}-> {o[0]}:{o[1]}" for s, t, o in extra_rels),
        "graph": {
            "nodes": [{"label": n.label, "name": n.properties.get("name")} for n in graph.nodes],
            "relationships": rel_view,
        },
    }


def rebuild_graph(stored: dict) -> Neo4jGraph:
    """round-N.json에 저장한 정리 후 그래프를 다시 Neo4jGraph로 만듦(--rescore용)."""
    nodes, ids = [], {}
    for i, n in enumerate(stored["nodes"]):
        ids.setdefault((n["label"], n["name"]), str(i))
        nodes.append({"id": ids[(n["label"], n["name"])], "label": n["label"], "properties": {"name": n["name"]}})
    rels = [{"start_node_id": ids[tuple(r["start"])], "end_node_id": ids[tuple(r["end"])], "type": r["type"]}
            for r in stored["relationships"]]
    return Neo4jGraph.model_validate({"nodes": nodes, "relationships": rels})


# ---------------------------------------------------------------------------
# 역량질문 답 가능 검사
# ---------------------------------------------------------------------------
def parse_path(path: str) -> list[list[str]]:
    """'A -R-> B -R2-> C ; X -R-> Y'를 [[A, R, B, R2, C], [X, R, Y]]로 바꿈."""
    chains = []
    for part in path.split(";"):
        toks = re.findall(r"-([A-Z_]+)->|([A-Za-z]+)", part)
        chain = [rel or node for rel, node in toks]
        if chain:
            chains.append(chain)
    return chains


def find_chains(chain: list[str], rels: list[tuple], start=None) -> list[list[tuple]]:
    """chain(타입·관계 번갈아)과 맞는 노드 경로를 모두 찾음."""
    results = []

    def walk(i: int, node, acc: list):
        if i == len(chain) - 1:
            results.append(acc)
            return
        rel_type, next_type = chain[i + 1], chain[i + 2]
        for s, t, o in rels:
            if s == node and t == rel_type and o[0] == next_type:
                walk(i + 2, o, acc + [o])

    starts = {s for s, _, _ in rels if s[0] == chain[0]} if start is None else {start}
    for s in starts:
        walk(0, s, [s])
    return results


def cq_answerable(cq_paths: list[dict], all_rels: list[tuple]) -> list[dict]:
    """경로의 모든 사슬(`;`로 구분)이 1건 이상 이어지면 답 가능.

    이후 사슬의 시작 타입이 첫 사슬의 둘째 이후 노드 타입이면(예: CQ04의 Procedure) 그 노드를 공유해야 함.
    첫 사슬의 시작 타입과 같으면(예: CQ01의 Invention 두 갈래) 서로 다른 노드에서 시작해도 됨.
    """
    out = []
    for cq in cq_paths:
        chains = parse_path(cq["path"])
        ok, example = False, ""
        for inst in find_chains(chains[0], all_rels):
            good, parts = True, [" → ".join(n[1] for n in inst)]
            for extra in chains[1:]:
                anchor = next((n for n in inst[1:] if n[0] == extra[0]), None)
                found = find_chains(extra, all_rels, start=anchor) if anchor else find_chains(extra, all_rels)
                if not found:
                    good = False
                    break
                parts.append(" → ".join(n[1] for n in found[0]))
            if good:
                ok, example = True, " / ".join(parts)
                break
        out.append({"cq_id": cq["cq_id"], "path": cq["path"], "answerable": ok, "example": example})
    return out


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------
def fmt(v: float) -> str:
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"


def render_report() -> str:
    rounds = sorted(WORK_DIR.glob("round-*.json"), key=lambda p: int(p.stem.split("-")[1]))
    data = [json.loads(p.read_text(encoding="utf-8")) for p in rounds]
    lines = [
        "# 채우기 시험(fill test) 보고서",
        "",
        "- 채우기 시험: 스키마 초안대로 시험용 청크에서 실제로 노드·관계를 뽑아 보고, 사람이 확정한 기대 결과와 비교해 스키마가 쓸 만한지 확인하는 시험",
        f"- 추출: `LLMEntityRelationExtractor`({MODEL_NAME}) → `GraphPruning.run(graph, schema)`"
        "(`additional_node_types`·`additional_relationship_types`·`additional_patterns` 모두 False)",
        f"- 기준: 재현율(recall, 기대 항목 중 뽑힌 비율) ≥ {RECALL_MIN}, 지어냄 비율(hallucination rate, 뽑힌 항목 중 기대에 없는 비율) ≤ {HALLUCINATION_MAX}, 역량질문 모두 답 가능",
        "- 항목: 노드(타입, 이름) + 관계(시작, 관계 타입, 끝). 이름은 공백·문장부호·괄호 속 한자를 지우고 비교하며, 기대 결과의 aliases(사람이 인정한 다른 이름)도 같은 이름으로 봄",
        "- `NOT_IN_SCHEMA`: 스키마에 없어 정리(pruning) 단계에서 지워진 항목. 스키마에 빠진 후보의 신호",
        "",
        "## 1. 회차별 지표",
        "",
        "| 회차 | 측정 | 재현율 | 지어냄 비율 | 역량질문 답 가능 | 기대 / 뽑힘 / 일치 | 판정 |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in data:
        m = d["metrics"]
        rep = d.get("repeat", 1)
        rr = f" ({m['recall_range'][0]:.3f} ~ {m['recall_range'][1]:.3f})" if rep > 1 else ""
        hr = f" ({m['hallucination_range'][0]:.3f} ~ {m['hallucination_range'][1]:.3f})" if rep > 1 else ""
        how = f"{rep}회 평균" if rep > 1 else "1회"
        lines.append(
            f"| {d['round']} | {how} | {m['recall']:.3f}{rr} | {m['hallucination_rate']:.3f}{hr} | "
            f"{m['cq_answerable']}/{m['cq_total']} | {m['expected']} / {fmt(m['extracted'])} / {fmt(m['hit'])} | "
            f"{'통과' if d['passed'] else '미달'} |"
        )
    decision = WORK_DIR / "final-decision.json"
    if decision.exists():
        fd = json.loads(decision.read_text(encoding="utf-8"))
        lines += ["", "## 2. 최종 결정", "", f"- {fd['date']} {fd['decision']}"]
        lines += [f"- 한계: {x}" for x in fd["limitations"]]
    for d in data:
        m = d["metrics"]
        lines += ["", f"## {d['round']}회차 상세", ""]
        if d.get("label"):
            lines.append(f"- 구분: {d['label']}")
        if d.get("repeat", 1) > 1:
            per_run = ", ".join(
                f"재현율 {r['metrics']['recall']:.3f} / 지어냄 {r['metrics']['hallucination_rate']:.3f} / "
                f"역량질문 {r['metrics']['cq_answerable']}/{r['metrics']['cq_total']}" for r in d["runs"])
            lines.append(f"- 측정: 같은 설정으로 {d['repeat']}회 추출. 재현율·지어냄 비율은 평균, "
                         f"역량질문은 과반 실행에서 이어지면 답 가능. 실행별: {per_run}")
        lines += [f"- 스키마 초안 출처: {d['schema_source']}", f"- 추출 프롬프트: {d.get('prompt_template', '라이브러리 기본값(ERExtractionTemplate)')}",
                  f"- 고친 내용: {d['changes']}", ""]
        if d.get("notes"):
            lines += ["### 원인 추정", ""] + [f"- {x}" for x in d["notes"]] + [""]
        lines += [
                  "### 청크별 결과", "", "| 청크 | 구분 | 기대 | 뽑힘 | 일치 | 지어냄 |", "|---|---|---|---|---|---|"]
        for c in d["chunks"]:
            lines.append(f"| {c['chunk_index']} | {c['kind']} | {c['expected']} | {fmt(c['extracted'])} | {fmt(c['hit'])} | {fmt(c['extra'])} |")
        lines += ["", "### 역량질문 답 가능", "", "| ID | 패턴 경로 | 답 가능 | 이어진 예 |", "|---|---|---|---|"]
        lines += [f"| {q['cq_id']} | `{q['path']}` | {'O' if q['answerable'] else 'X'}"
                  f"{' (' + q['runs_ok'] + ')' if q.get('runs_ok') else ''} | {q['example']} |" for q in d["cq"]]
        lines += ["", "### NOT_IN_SCHEMA로 지워진 항목(타입별)", ""]
        pr = d["pruned_not_in_schema"]
        if not (pr["nodes"] or pr["relationships"]):
            lines.append("- 없음")
        lines += [f"- 노드 `{k}`: {v}건" for k, v in pr["nodes"].items()]
        lines += [f"- 관계 `{k}`: {v}건" for k, v in pr["relationships"].items()]
        lines += ["", "### 놓친 항목과 지어낸 항목", ""]
        for c in d["chunks"]:
            if c["missed_nodes"] or c["missed_rels"] or c["extra_nodes"] or c["extra_rels"]:
                lines.append(f"- chunk {c['chunk_index']}")
                lines += [f"  - 놓침: {x}" for x in c["missed_nodes"] + c["missed_rels"]]
                lines += [f"  - 지어냄: {x}" for x in c["extra_nodes"] + c["extra_rels"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
async def extract_run(schema: GraphSchema, expected: dict, prompt_template: str | None) -> tuple[list, list, Counter, Counter]:
    """시험용 청크 전체를 한 번 추출하고 정리(pruning)함."""
    by_idx = {c["chunk_index"]: c for c in load_chunks()}
    extra = {"prompt_template": (SCHEMA_DIR / prompt_template).read_text(encoding="utf-8")} if prompt_template else {}
    extractor = LLMEntityRelationExtractor(llm=make_llm(reasoning_effort="medium"), create_lexical_graph=False, **extra)
    pruner = GraphPruning()
    graphs = await asyncio.gather(*(extract_chunk(extractor, schema, by_idx[c["chunk_index"]]) for c in expected["chunks"]))
    pruned_graphs, pruning_texts = [], []
    pruned_nodes, pruned_rels = Counter(), Counter()
    for graph in graphs:
        pruned = await pruner.run(graph=graph, schema=schema)
        for p in pruned.pruning_stats.pruned_nodes:
            if p.pruned_reason == PruningReason.NOT_IN_SCHEMA:
                pruned_nodes[p.label] += 1
        for p in pruned.pruning_stats.pruned_relationships:
            if p.pruned_reason == PruningReason.NOT_IN_SCHEMA:
                pruned_rels[p.label] += 1
        pruned_graphs.append(pruned.graph)
        pruning_texts.append(str(pruned.pruning_stats))
    return pruned_graphs, pruning_texts, pruned_nodes, pruned_rels


def score_run(draft: dict, expected: dict, pruned_graphs: list, pruning_texts: list,
              pruned_nodes: Counter, pruned_rels: Counter) -> dict:
    """한 번 추출한 결과를 채점함."""
    chunks_out, all_rels = [], []
    for exp, graph, pruning in zip(expected["chunks"], pruned_graphs, pruning_texts):
        s = score_chunk(graph, exp)
        _, rels, _ = graph_items(graph)
        all_rels += list(rels)
        chunks_out.append({"chunk_index": exp["chunk_index"], "kind": exp["kind"], **s, "pruning": pruning})
    expected_n = sum(c["expected"] for c in chunks_out)
    extracted_n = sum(c["extracted"] for c in chunks_out)
    hit_n = sum(c["hit"] for c in chunks_out)
    recall = hit_n / expected_n if expected_n else 0.0
    halluc = (extracted_n - hit_n) / extracted_n if extracted_n else 0.0
    cq = cq_answerable(draft["cq_paths"], all_rels)
    n_ok = sum(q["answerable"] for q in cq)
    return {
        "metrics": {"recall": recall, "hallucination_rate": halluc, "cq_answerable": n_ok, "cq_total": len(cq),
                    "expected": expected_n, "extracted": extracted_n, "hit": hit_n},
        "passed": recall >= RECALL_MIN and halluc <= HALLUCINATION_MAX and n_ok == len(cq),
        "cq": cq,
        "pruned_not_in_schema": {"nodes": dict(pruned_nodes), "relationships": dict(pruned_rels)},
        "chunks": chunks_out,
    }


def aggregate_runs(runs: list[dict]) -> dict:
    """반복 실행 결과를 평균으로 묶음. 역량질문은 과반 실행에서 이어지면 답 가능."""
    n = len(runs)

    def mean(xs: list[float]) -> float:
        return sum(xs) / len(xs)

    rec = [r["metrics"]["recall"] for r in runs]
    hal = [r["metrics"]["hallucination_rate"] for r in runs]
    cq = []
    for i, q in enumerate(runs[0]["cq"]):
        oks = [r["cq"][i] for r in runs if r["cq"][i]["answerable"]]
        cq.append({**q, "answerable": len(oks) * 2 > n, "runs_ok": f"{len(oks)}/{n}",
                   "example": oks[0]["example"] if oks else ""})
    n_ok = sum(q["answerable"] for q in cq)
    chunks = []
    for j, c0 in enumerate(runs[0]["chunks"]):
        cs = [r["chunks"][j] for r in runs]

        def freq(key: str) -> list[str]:
            cnt = Counter(x for c in cs for x in c[key])
            return [f"{x} ({k}/{n})" for x, k in cnt.most_common()]

        chunks.append({"chunk_index": c0["chunk_index"], "kind": c0["kind"], "expected": c0["expected"],
                       "extracted": mean([c["extracted"] for c in cs]), "hit": mean([c["hit"] for c in cs]),
                       "extra": mean([c["extra"] for c in cs]),
                       "missed_nodes": freq("missed_nodes"), "missed_rels": freq("missed_rels"),
                       "extra_nodes": freq("extra_nodes"), "extra_rels": freq("extra_rels")})
    pn, pr = Counter(), Counter()
    for r in runs:
        pn.update(r["pruned_not_in_schema"]["nodes"])
        pr.update(r["pruned_not_in_schema"]["relationships"])
    return {
        "metrics": {"recall": mean(rec), "hallucination_rate": mean(hal), "cq_answerable": n_ok, "cq_total": len(cq),
                    "expected": runs[0]["metrics"]["expected"],
                    "extracted": mean([r["metrics"]["extracted"] for r in runs]),
                    "hit": mean([r["metrics"]["hit"] for r in runs]),
                    "recall_range": [min(rec), max(rec)], "hallucination_range": [min(hal), max(hal)]},
        "passed": mean(rec) >= RECALL_MIN and mean(hal) <= HALLUCINATION_MAX and n_ok == len(cq),
        "cq": cq,
        "pruned_not_in_schema": {"nodes": dict(pn), "relationships": dict(pr)},
        "chunks": chunks,
        "runs": [{"metrics": r["metrics"], "passed": r["passed"], "cq": r["cq"], "chunks": r["chunks"]} for r in runs],
    }


async def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--rescore", action="store_true")
    parser.add_argument("--prompt-template", help="LLMEntityRelationExtractor에 넣을 추출 프롬프트 파일(없으면 라이브러리 기본값)")
    parser.add_argument("--repeat", type=int, default=1, help="같은 설정으로 반복 추출할 횟수(2 이상이면 평균으로 판정)")
    parser.add_argument("--schema-file", default=DRAFT_FILE.name, help="확정된 스키마 초안 파일(기본 schema-draft.json)")
    parser.add_argument("--label", default="", help="보고서에 붙일 설명")
    args = parser.parse_args()
    precheck(need_llm=not args.rescore)

    draft = load_json_confirmed(SCHEMA_DIR / args.schema_file, "스키마 초안")
    expected = load_json_confirmed(EXPECTED_FILE, "기대 추출 결과")
    load_competency_questions()  # 역량질문 확정 여부 확인
    schema = GraphSchema.model_validate(draft["schema"])
    assert not (schema.additional_node_types or schema.additional_relationship_types or schema.additional_patterns)

    prev: dict = {}
    if args.rescore:
        prev = json.loads((WORK_DIR / f"round-{args.round}.json").read_text(encoding="utf-8"))
        stored = {c["chunk_index"]: c for c in prev["chunks"]}
        graphs = [rebuild_graph(stored[c["chunk_index"]]["graph"]) for c in expected["chunks"]]
        texts = [stored[c["chunk_index"]]["pruning"] for c in expected["chunks"]]
        scored = score_run(draft, expected, graphs, texts,
                           Counter(prev["pruned_not_in_schema"]["nodes"]),
                           Counter(prev["pruned_not_in_schema"]["relationships"]))
    else:
        runs = []
        for i in range(args.repeat):
            scored_i = score_run(draft, expected, *await extract_run(schema, expected, args.prompt_template))
            m = scored_i["metrics"]
            print(f"  실행 {i + 1}/{args.repeat}: 재현율 {m['recall']:.3f}, 지어냄 비율 {m['hallucination_rate']:.3f}, "
                  f"역량질문 {m['cq_answerable']}/{m['cq_total']}")
            runs.append(scored_i)
        scored = runs[0] if args.repeat == 1 else aggregate_runs(runs)

    result = {
        "round": args.round,
        "label": args.label or prev.get("label", ""),
        "repeat": prev.get("repeat", 1) if args.rescore else args.repeat,
        "schema_file": args.schema_file,
        "schema_round": draft["round"],
        "schema_source": draft.get("source", "LLM"),
        "changes": draft.get("changes", "최초 초안"),
        "prompt_template": args.prompt_template or prev.get("prompt_template") or "라이브러리 기본값(ERExtractionTemplate)",
        **scored,
    }
    if prev.get("notes"):
        result["notes"] = prev["notes"]
    chunks_out, cq = result["chunks"], result["cq"]
    # 다음 회차 초안(step5 --round N+1)에 줄 요약
    result["feedback"] = {
        "metrics": result["metrics"],
        "cq_not_answerable": [q for q in cq if not q["answerable"]],
        "missed": {c["chunk_index"]: c["missed_nodes"] + c["missed_rels"]
                   for c in chunks_out if c["missed_nodes"] or c["missed_rels"]},
        "hallucinated": {c["chunk_index"]: c["extra_nodes"] + c["extra_rels"]
                         for c in chunks_out if c["extra_nodes"] or c["extra_rels"]},
        "pruned_not_in_schema": result["pruned_not_in_schema"],
    }
    write_json(WORK_DIR / f"round-{args.round}.json", result)
    REPORT_FILE.write_text(render_report(), encoding="utf-8")
    m = result["metrics"]
    tag = f"{result['repeat']}회 평균" if result["repeat"] > 1 else "1회"
    print(f"[{args.round}회차, {tag}] 재현율 {m['recall']:.3f} (기준 {RECALL_MIN}↑), 지어냄 비율 {m['hallucination_rate']:.3f} "
          f"(기준 {HALLUCINATION_MAX}↓), 역량질문 답 가능 {m['cq_answerable']}/{m['cq_total']} → {'통과' if result['passed'] else '미달'}")


if __name__ == "__main__":
    asyncio.run(main())
