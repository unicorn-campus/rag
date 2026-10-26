"""최적 추출 방안으로 191청크 전체를 실험용 Neo4j(bolt://localhost:7688)에 색인함.

원래 색인(graphrag/index/index_graph.py)의 K4 ~ K8 함수(적재, 병합, 임베딩, 커뮤니티 요약)와 적재 검증(verify_graph.py)을
읽기만 하고 가져다 씀. 추출(K2·K3)만 실험 방안으로 바꿈. 원래 그래프(7687)에는 쓰지 않음(접속 주소 안전장치).

실행(저장소 루트에서):
    python graphrag/experiments/agentic/index_agentic.py --pipeline vote
    python graphrag/experiments/agentic/index_agentic.py --pipeline vote --resume   # 이미 추출한 청크는 건너뜀
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time

from harness import EXP_DIR, RESULTS_DIR, SCHEMA_DRAFT, Context, load_chunks_cached
from common import ENV_FILE, load_json_confirmed, setup_console, write_json
import pipelines

sys.path.insert(0, str(EXP_DIR.parents[1] / "index"))
import index_graph  # noqa: E402  (원래 색인 함수, 읽기만 함)
import verify_graph  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from neo4j_graphrag.components.schema import GraphSchema  # noqa: E402
from neo4j_graphrag.components.types import Neo4jGraph, Neo4jNode, Neo4jRelationship  # noqa: E402

AGENTIC_URI = "bolt://localhost:7688"
SOURCE = "특허법.pdf"
log = index_graph.log   # 원래 함수들이 쓰는 로거에 이 실험의 로그 파일만 붙임


def setup_log(path) -> None:
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for h in (logging.FileHandler(path, mode="w", encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("neo4j_graphrag").setLevel(logging.ERROR)


def connect():
    import neo4j

    load_dotenv(ENV_FILE)
    uri = os.getenv("AGENTIC_NEO4J_URI", AGENTIC_URI)
    if uri.rstrip("/").endswith(":7687"):
        sys.exit("[중단] 원래 실습 그래프(7687)에는 쓰지 않음. 실험용 Neo4j(7688)를 쓸 것")
    _, password = index_graph.neo4j_settings()
    driver = neo4j.GraphDatabase.driver(uri, auth=(index_graph.NEO4J_USER, password), notifications_min_severity="OFF")
    driver.verify_connectivity()
    return driver, uri


def with_lexical(graph: Neo4jGraph, chunk_index: int, text: str) -> Neo4jGraph:
    """원래 색인과 같은 모양의 청크 노드와 '엔티티 → 청크'(FROM_CHUNK) 연결을 붙이고, 노드 ID를 청크마다 고유하게 바꿈."""
    cid = f"{SOURCE}::{chunk_index:05d}"
    ids = {n.id: f"{cid}:{n.id}" for n in graph.nodes}
    nodes = [Neo4jNode(id=ids[n.id], label=n.label, properties=n.properties) for n in graph.nodes]
    rels = [Neo4jRelationship(start_node_id=ids[r.start_node_id], end_node_id=ids[r.end_node_id], type=r.type,
                              properties=r.properties) for r in graph.relationships
            if r.start_node_id in ids and r.end_node_id in ids]
    chunk = Neo4jNode(id=cid, label="Chunk", properties={"text": text, "index": chunk_index, "source": SOURCE,
                                                         "chunk_index": chunk_index})
    rels += [Neo4jRelationship(start_node_id=n.id, end_node_id=cid, type="FROM_CHUNK") for n in nodes]
    return Neo4jGraph(nodes=[chunk] + nodes, relationships=rels)


async def extract_all(name: str, resume: bool) -> list[dict]:
    """K2·K3 대신: 실험 방안으로 청크마다 추출해 results/index-{방안}/chunk-N.json에 보관."""
    out_dir = RESULTS_DIR / f"index-{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    draft = load_json_confirmed(SCHEMA_DRAFT, "스키마 초안")
    chunks = load_chunks_cached()
    ctx = Context(GraphSchema.model_validate(draft["schema"]), draft, chunks)
    pipeline = pipelines.PIPELINES[name]
    records, done = {}, 0

    async def one(ci: int) -> None:
        nonlocal done
        path = out_dir / f"chunk-{ci}.json"
        if resume and path.exists():
            records[ci] = json.loads(path.read_text(encoding="utf-8"))
        else:
            g = await pipeline(ctx, {"chunk_index": ci, "text": chunks[ci]})
            rec = {"chunk_index": ci, "graph": with_lexical(g, ci, chunks[ci]).model_dump(),
                   "entity_nodes": len(g.nodes), "entity_relationships": len(g.relationships)}
            write_json(path, rec)
            records[ci] = rec
        done += 1
        if done % 20 == 0 or done == len(chunks):
            log.info(f"  [추출] {done}/{len(chunks)} 청크, LLM {ctx.calls}회, 실패 {len(ctx.failures)}")

    started = time.time()
    await asyncio.gather(*(one(ci) for ci in sorted(chunks)))
    ordered = [records[k] for k in sorted(records)]
    log.info(f"[추출:{name}] 청크 {len(ordered)}건, 엔티티 {sum(r['entity_nodes'] for r in ordered)}개·관계 "
             f"{sum(r['entity_relationships'] for r in ordered)}개, LLM {ctx.calls}회, 실패 {ctx.failures or '없음'}, "
             f"{time.time() - started:.0f}초")
    write_json(out_dir / "_run.json", {"llm_calls": ctx.calls, "failures": ctx.failures})
    return ordered


def clear(driver) -> None:
    """실험용 그래프만 비움(접속 주소는 connect()에서 7688로 확인함)."""
    with driver.session() as s:
        s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 1000 ROWS").consume()
        for r in list(s.run("SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP' RETURN name")):
            s.run(f"DROP INDEX `{r['name']}` IF EXISTS").consume()


def write_report(name: str, uri: str, records: list[dict], merge: dict, emb: dict, com: dict, summ: dict, result: dict) -> None:
    lines = [f"# 실험 색인 보고서: {name}", "", f"- 그래프: {uri} (실험용, 원래 실습 그래프와 별도)",
             f"- 추출: 실험 방안 `{name}`(pipelines.py), 적재·병합·임베딩·커뮤니티는 원래 색인 함수 그대로", "",
             "| 항목 | 결과 |", "|---|---|",
             f"| 청크 | {len(records)}건 |",
             f"| 추출 엔티티·관계 | {sum(r['entity_nodes'] for r in records)}개 · {sum(r['entity_relationships'] for r in records)}개 |",
             f"| 병합 | 엔티티 {merge['before']} → {merge['after']}개 |",
             f"| 고립 엔티티 | {result['isolated_entities']}개 |",
             f"| 엔티티 임베딩 | {emb['entities']}개, {emb['dimensions']}차원 |",
             f"| 커뮤니티 | {com['communities']}개(2개 이상 {com['multi']}개), 요약 {summ['summarized']}/{summ['targets']}개 |",
             f"| 역량질문 패턴 경로 | {result['cq_pass']}/{len(result['cq'])} |", "",
             "| 노드 라벨 | 개수 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in result["nodes"].items()]
    lines += ["", "| 관계 타입 | 개수 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in result["relationships"].items()]
    lines += ["", "| 역량질문 | 조회 건수 | 질문 대상 건수 |", "|---|---|---|"]
    lines += [f"| {c['id']} | {' / '.join(str(p['count']) for p in c['parts'])} | {' / '.join(str(p['anchored']) for p in c['parts'])} |"
              for c in result["cq"]]
    (EXP_DIR / f"index-report-{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    setup_console()
    p = argparse.ArgumentParser()
    p.add_argument("--pipeline", required=True, choices=list(pipelines.PIPELINES))
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()
    setup_log(RESULTS_DIR / f"index-{args.pipeline}.log")
    driver, uri = connect()
    try:
        log.info(f"===== 실험 색인 시작: {args.pipeline} → {uri} =====")
        records = await extract_all(args.pipeline, args.resume)
        clear(driver)
        await index_graph.write_graph(driver, records)                       # K4
        merge = await index_graph.merge_entities(driver)                       # K5
        embedder = index_graph.make_embedder()
        emb = index_graph.embed_entities(driver, embedder)                     # K6
        com = index_graph.detect_communities(driver)                           # K8
        summ = await index_graph.summarize_communities(driver, embedder, 4)
        result = verify_graph.verify(driver, index_graph.DESIGN_FILE)
        verify_graph.log_summary(log, result)
        write_report(args.pipeline, uri, records, merge, emb, com, summ, result)
        log.info(f"===== 완료: index-report-{args.pipeline}.md =====")
    finally:
        driver.close()


if __name__ == "__main__":
    asyncio.run(main())
