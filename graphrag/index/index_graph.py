"""특허법 GraphRAG 지식그래프(KG) 인덱스 구축.

벡터DB 청크 → K1 추출 단위 → K2 엔티티·관계 추출 → K3 가지치기·결과 보관
→ K4 Neo4j 적재 → K5 엔티티 병합 → K6 엔티티 설명 임베딩 → K8 커뮤니티 요약 → 적재 검증·보고서.
전체 색인: 실행할 때마다 그래프를 비우고 처음부터 다시 만듦(K7 청크 정리는 생략).
API Key와 Neo4j 비밀번호 값은 화면·로그·파일에 남기지 않음(환경변수로만 전달).

실행 예:
    python graphrag/index/index_graph.py --limit 5     # 시범 실행(청크 5건)
    python graphrag/index/index_graph.py               # 전체 청크 색인
    python graphrag/index/index_graph.py --from-extracted   # 보관한 추출 결과로 적재부터 다시
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv

INDEX_DIR = Path(__file__).resolve().parent             # rag/graphrag/index
sys.path.insert(0, str(INDEX_DIR.parent / "schema"))    # 스키마 단계 공통 모듈 재사용

import common as schema_common  # noqa: E402  (graphrag/schema/common.py)
from common import COLLECTION_NAME, ENV_FILE, PROJECT_ROOT, SCHEMA_FILE, VECTORDB_DIR  # noqa: E402

import verify_graph  # noqa: E402

# ---------------------------------------------------------------------------
# 경로 및 설정
# ---------------------------------------------------------------------------
EXTRACTED_DIR = INDEX_DIR / "extracted"
FAILED_FILE = EXTRACTED_DIR / "failed-chunks.json"
PRUNING_STATS_FILE = INDEX_DIR / "pruning-stats.json"
REPORT_FILE = INDEX_DIR / "index-report.md"
LOG_FILE = INDEX_DIR / "index-run.log"
NEO4J_ENV_FILE = PROJECT_ROOT / "neo4j" / ".env"
DESIGN_FILE = INDEX_DIR.parent / "schema" / "schema-design.md"

NEO4J_URI_DEFAULT = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
EMBEDDING_MODEL = "embed-v4.0"          # 벡터DB와 같은 모델
EMBED_BATCH_SIZE = 96                   # Cohere Embed API의 요청당 최대 텍스트 수
ENTITY_INDEX = "entity_embedding"
COMMUNITY_INDEX = "community_embedding"
GDS_GRAPH = "patent_entities"           # GDS 메모리 그래프(투영) 이름
LEIDEN_SEED = 42
RESERVED_LABELS = ["__Entity__", "__KGBuilder__"]

EXTRACT_RETRIES = 3                     # 형식 오류 등 일반 실패 재시도 횟수
RATE_LIMIT_RETRIES = 5                  # Groq 429가 라이브러리 재시도 뒤에도 남을 때 추가로 기다리는 횟수
RATE_LIMIT_WAIT_SEC = 60

log = logging.getLogger("graphrag.index")


# ---------------------------------------------------------------------------
# 공통: 로그, Neo4j 접속, Cohere 임베딩
# ---------------------------------------------------------------------------
def setup_logging() -> None:
    """화면과 index-run.log에 같은 실행 로그를 남김(매 실행마다 새로 씀)."""
    schema_common.setup_console()
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
    file_handler = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
    console = logging.StreamHandler(sys.stdout)
    for h in (file_handler, console):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False
    logging.getLogger("neo4j").setLevel(logging.ERROR)   # 드라이버 알림 메시지 숨김
    logging.getLogger("httpx").setLevel(logging.WARNING)


def neo4j_settings() -> tuple[str, str]:
    """접속 주소와 비밀번호. 비밀번호는 환경변수 → neo4j/.env 순으로 찾고 값은 출력하지 않음."""
    uri = os.getenv("NEO4J_URI", NEO4J_URI_DEFAULT)
    password = os.getenv("NEO4J_PASSWORD") or (dotenv_values(NEO4J_ENV_FILE).get("NEO4J_PASSWORD") if NEO4J_ENV_FILE.exists() else None)
    return uri, (password or "").strip()


def make_embedder():
    """Cohere embed-v4.0 임베더(색인 쪽이므로 input_type=search_document)."""
    from neo4j_graphrag.embeddings import CohereEmbeddings

    return CohereEmbeddings(model=EMBEDDING_MODEL, input_type="search_document", api_key=os.environ["COHERE_API_KEY"])


def embed_texts(embedder, texts: list[str]) -> list[list[float]]:
    """텍스트를 96건씩 묶어 임베딩함(호출 수를 줄이기 위해 Cohere 클라이언트의 일괄 호출을 씀).

    Cohere 호출 한도(429)에 걸리면 1분 쉬고 다시 시도함.
    """
    from cohere.errors import TooManyRequestsError

    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        for attempt in range(1, RATE_LIMIT_RETRIES + 1):
            try:
                res = embedder.client.embed(texts=batch, model=embedder.model, input_type=embedder.input_type)
                break
            except TooManyRequestsError:
                log.info(f"  [rate-limit] Cohere 호출 한도 초과 → {RATE_LIMIT_WAIT_SEC}초 대기 후 재시도({attempt}/{RATE_LIMIT_RETRIES})")
                time.sleep(RATE_LIMIT_WAIT_SEC)
        else:
            raise RuntimeError("Cohere 임베딩 호출 한도 초과가 계속됨")
        vectors.extend([list(v) for v in res.embeddings])
    return vectors


# ---------------------------------------------------------------------------
# 사전 검사
# ---------------------------------------------------------------------------
def precheck():
    """벡터DB, 스키마, API Key, Neo4j(+GDS)를 확인함. 하나라도 안 되면 안내 후 종료.

    Returns: (neo4j 드라이버, GraphSchema)
    """
    import neo4j
    from neo4j_graphrag.components.schema import GraphSchema

    # 벡터DB(컬렉션·청크 수)와 GROQ_API_KEY는 스키마 단계의 검사를 그대로 씀
    schema_common.precheck(need_llm=True)

    try:
        schema = GraphSchema.from_file(str(SCHEMA_FILE))
    except Exception as e:  # 파일 없음, 형식 오류
        sys.exit(
            f"[사전 검사 실패] `{SCHEMA_FILE.relative_to(PROJECT_ROOT).as_posix()}`이 없거나 GraphSchema로 불러지지 않음({type(e).__name__}).\n"
            "  → `prompts/graphrag-schema.md`를 먼저 수행할 것"
        )

    load_dotenv(ENV_FILE)
    missing = [k for k in ("GROQ_API_KEY", "COHERE_API_KEY") if not os.getenv(k, "").strip()]
    if missing:
        sys.exit(f"[사전 검사 실패] `.env`에 비어 있는 키: {', '.join(missing)}")

    uri, password = neo4j_settings()
    if not password:
        sys.exit("[사전 검사 실패] NEO4J_PASSWORD가 비어 있음 → `neo4j/.env`에 NEO4J_PASSWORD를 넣을 것(neo4j/README.md 참고)")
    driver = neo4j.GraphDatabase.driver(uri, auth=(NEO4J_USER, password), notifications_min_severity="OFF")
    try:
        driver.verify_connectivity()
        gds_version = driver.execute_query("RETURN gds.version() AS v").records[0]["v"]
    except Exception as e:
        driver.close()
        sys.exit(
            f"[사전 검사 실패] Neo4j({uri}) 접속 또는 GDS 확인 실패({type(e).__name__}).\n"
            "  → `neo4j/README.md`대로 컨테이너를 띄울 것: docker compose -f neo4j/compose.yml up -d\n"
            "    상태 확인: docker compose -f neo4j/compose.yml ps (healthy가 되면 다시 실행)"
        )
    log.info(f"[사전 검사] 스키마: 노드 {len(schema.node_types)}·관계 {len(schema.relationship_types)}·패턴 {len(schema.patterns)}, "
             f"API Key: GROQ·COHERE 설정됨, Neo4j: {uri} 접속됨(GDS {gds_version})")
    return driver, schema


# ---------------------------------------------------------------------------
# 그래프 비우기(전체 색인 준비)
# ---------------------------------------------------------------------------
def clear_graph(driver, assume_yes: bool) -> None:
    """기존 노드가 있으면 개수를 보여 주고 확인을 받은 뒤 노드·관계·인덱스를 모두 지움."""
    count = driver.execute_query("MATCH (n) RETURN count(n) AS c").records[0]["c"]
    indexes = [r["name"] for r in driver.execute_query(
        "SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP' RETURN name").records]
    if count == 0 and not indexes:
        log.info("[그래프 비우기] 기존 노드·인덱스 없음")
        return
    labels = driver.execute_query(
        "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c ORDER BY c DESC LIMIT 10").records
    log.info(f"[그래프 비우기] 기존 노드 {count}개, 인덱스 {len(indexes)}개({', '.join(indexes) or '-'})")
    log.info("  라벨별(상위 10): " + ", ".join(f"{r['l']} {r['c']}" for r in labels))
    if not assume_yes:
        if not sys.stdin.isatty():
            sys.exit("[중단] 기존 데이터를 지우려면 확인이 필요함. 터미널에서 실행하거나 확인 후 --yes를 붙일 것")
        if input("  모두 지우고 처음부터 색인할까요? [y/N] ").strip().lower() != "y":
            sys.exit("[중단] 기존 데이터를 지우지 않음")
    with driver.session() as session:
        session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 1000 ROWS").consume()
        for name in indexes:
            session.run(f"DROP INDEX `{name}` IF EXISTS").consume()
        session.run("CALL gds.graph.drop($g, false) YIELD graphName RETURN graphName", g=GDS_GRAPH).consume()
    log.info(f"[그래프 비우기] 노드 {count}개와 인덱스 {len(indexes)}개를 지움")


# ---------------------------------------------------------------------------
# K1. 추출 단위 나누기: 벡터DB 청크를 그대로 씀
# ---------------------------------------------------------------------------
def load_extraction_units(limit: int | None) -> list[dict[str, Any]]:
    """벡터DB 청크를 chunk_index 순으로 읽어 추출 단위(chunk_index, source, 본문)로 씀.

    청크 ID는 벡터DB 문서 ID와 같은 `{source}::{chunk_index:05d}`로 맞춰 두 저장소를 이어 볼 수 있게 함.
    """
    import chromadb

    client = chromadb.PersistentClient(path=str(VECTORDB_DIR))
    res = client.get_collection(COLLECTION_NAME).get(include=["documents", "metadatas"])
    units = sorted(
        (
            {
                "chunk_index": int(m["chunk_index"]),
                "source": m["source"],
                "chunk_id": f"{m['source']}::{int(m['chunk_index']):05d}",
                "text": d,
            }
            for d, m in zip(res["documents"], res["metadatas"])
        ),
        key=lambda u: (u["source"], u["chunk_index"]),
    )
    if limit:
        units = units[:limit]
    log.info(f"[K1 추출 단위] 벡터DB 청크 {len(units)}건(chunk_index {units[0]['chunk_index']} ~ {units[-1]['chunk_index']})")
    return units


def chunk_file(unit: dict[str, Any]) -> Path:
    return EXTRACTED_DIR / f"chunk-{unit['chunk_index']}.json"


# ---------------------------------------------------------------------------
# K2. 추출: 엔티티와 관계 추출(청크 노드와 "엔티티 → 청크" 연결 포함)
# ---------------------------------------------------------------------------
async def extract_chunk(extractor, schema, unit: dict[str, Any]):
    """청크 하나를 추출함. 형식 오류는 3회까지, Groq 호출 한도(429)는 1분씩 기다리며 5회까지 다시 시도함."""
    from neo4j_graphrag.components.types import TextChunk, TextChunks
    from neo4j_graphrag.exceptions import RateLimitError

    chunk = TextChunk(
        text=unit["text"],
        index=unit["chunk_index"],
        metadata={"source": unit["source"], "chunk_index": unit["chunk_index"]},
        uid=unit["chunk_id"],
    )
    errors, waits = 0, 0
    while True:
        try:
            return await extractor.run(chunks=TextChunks(chunks=[chunk]), schema=schema)
        except RateLimitError:
            waits += 1
            if waits > RATE_LIMIT_RETRIES:
                raise
            log.info(f"  [rate-limit] chunk {unit['chunk_index']}: Groq 호출 한도 초과 → {RATE_LIMIT_WAIT_SEC}초 대기({waits}/{RATE_LIMIT_RETRIES})")
            await asyncio.sleep(RATE_LIMIT_WAIT_SEC)
        except Exception:
            errors += 1
            if errors >= EXTRACT_RETRIES:
                raise


# ---------------------------------------------------------------------------
# K3. 가지치기: 스키마 밖 항목 제거 후 청크별 JSON으로 보관
# ---------------------------------------------------------------------------
def count_graph(graph) -> dict[str, int]:
    """그래프의 엔티티 노드·엔티티 관계 수(청크 노드와 FROM_CHUNK 연결은 따로 셈)."""
    entity_nodes = [n for n in graph.nodes if n.label != "Chunk"]
    entity_rels = [r for r in graph.relationships if r.type != "FROM_CHUNK"]
    return {"entity_nodes": len(entity_nodes), "entity_relationships": len(entity_rels),
            "chunk_nodes": len(graph.nodes) - len(entity_nodes),
            "from_chunk": len(graph.relationships) - len(entity_rels)}


def pruned_items(stats) -> list[dict[str, str]]:
    """PruningStats를 저장하기 쉬운 목록(종류, 타입, 사유, 이름)으로 바꿈."""
    items = []
    for p in stats.pruned_nodes:
        items.append({"kind": "node", "type": p.label, "reason": p.pruned_reason.value,
                      "name": str(p.item.properties.get("name", ""))})
    for p in stats.pruned_relationships:
        items.append({"kind": "relationship", "type": p.label, "reason": p.pruned_reason.value,
                      "name": f"{p.item.start_node_id} -> {p.item.end_node_id}"})
    for p in stats.pruned_properties:
        items.append({"kind": "property", "type": p.label, "reason": p.pruned_reason.value, "name": str(p.item)})
    return items


async def prune_and_save(pruner, schema, graph, unit: dict[str, Any]):
    """GraphPruning으로 정리하고 청크별 JSON(extracted/chunk-{chunk_index}.json)으로 저장함."""
    result = await pruner.run(graph=graph, schema=schema)
    record = {
        "chunk_index": unit["chunk_index"],
        "source": unit["source"],
        "chunk_id": unit["chunk_id"],
        "raw_counts": count_graph(graph),
        "kept_counts": count_graph(result.graph),
        "pruned": pruned_items(result.pruning_stats),
        "graph": result.graph.model_dump(),
    }
    schema_common.write_json(chunk_file(unit), record)
    return record


async def extract_and_prune(units: list[dict[str, Any]], schema, concurrency: int, resume: bool) -> tuple[list[dict], list[dict]]:
    """K2 + K3를 청크마다 이어서 수행함(끝난 청크는 바로 저장해 중단되어도 남음)."""
    from neo4j_graphrag.components.entity_relation_extractor import LLMEntityRelationExtractor, OnError
    from neo4j_graphrag.components.graph_pruning import GraphPruning

    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    if not resume:  # 전체 색인: 이전 보관본을 지우고 새로 만듦
        for f in EXTRACTED_DIR.glob("chunk-*.json"):
            f.unlink()
        FAILED_FILE.unlink(missing_ok=True)

    # 추출 프롬프트는 라이브러리 기본값(채우기 시험과 같은 조건). 청크 노드·FROM_CHUNK 연결을 함께 만듦
    extractor = LLMEntityRelationExtractor(
        llm=schema_common.make_llm(reasoning_effort="medium"),
        create_lexical_graph=True,
        on_error=OnError.RAISE,
    )
    pruner = GraphPruning()
    sem = asyncio.Semaphore(concurrency)
    records: dict[int, dict] = {}
    failed: list[dict] = []
    done = 0

    async def work(unit: dict[str, Any]) -> None:
        nonlocal done
        path = chunk_file(unit)
        if resume and path.exists():
            records[unit["chunk_index"]] = json.loads(path.read_text(encoding="utf-8"))
            done += 1
            return
        async with sem:
            try:
                graph = await extract_chunk(extractor, schema, unit)
                records[unit["chunk_index"]] = await prune_and_save(pruner, schema, graph, unit)
            except Exception as e:  # 다시 시도해도 실패한 청크는 건너뛰고 번호를 기록
                failed.append({"chunk_index": unit["chunk_index"], "error": f"{type(e).__name__}: {str(e)[:200]}"})
                log.info(f"  [실패] chunk {unit['chunk_index']}: {type(e).__name__}")
        done += 1
        if done % 10 == 0 or done == len(units):
            log.info(f"  [K2·K3] {done}/{len(units)} 청크 처리(실패 {len(failed)})")

    started = time.time()
    await asyncio.gather(*(work(u) for u in units))
    failed.sort(key=lambda f: f["chunk_index"])
    schema_common.write_json(FAILED_FILE, failed)
    ordered = [records[k] for k in sorted(records)]
    raw = Counter()
    for r in ordered:
        raw.update(r["raw_counts"])
    log.info(f"[K2 추출] 성공 {len(ordered)}건, 실패 {len(failed)}건({time.time() - started:.0f}초), "
             f"추출 원본 엔티티 {raw['entity_nodes']}개·관계 {raw['entity_relationships']}개")
    return ordered, failed


def load_extracted(units: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """--from-extracted: 보관한 청크별 JSON을 읽어 LLM을 다시 부르지 않음."""
    if not EXTRACTED_DIR.exists():
        sys.exit("[중단] extracted/가 없음. --from-extracted 없이 추출부터 실행할 것")
    records, missing = [], []
    for u in units:
        path = chunk_file(u)
        if path.exists():
            records.append(json.loads(path.read_text(encoding="utf-8")))
        else:
            missing.append(u["chunk_index"])
    failed = json.loads(FAILED_FILE.read_text(encoding="utf-8")) if FAILED_FILE.exists() else []
    failed_idx = {f["chunk_index"] for f in failed}
    failed += [{"chunk_index": i, "error": "보관된 추출 결과 없음"} for i in missing if i not in failed_idx]
    log.info(f"[K2 추출] 보관본 사용: {len(records)}건 읽음, 보관본 없음/실패 {len(failed)}건")
    return records, sorted(failed, key=lambda f: f["chunk_index"])


def summarize_pruning(records: list[dict]) -> dict[str, Any]:
    """청크별 지워진 항목을 사유별, 타입별로 셈 → pruning-stats.json."""
    by_reason: dict[str, Counter] = defaultdict(Counter)
    by_type: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    by_chunk = {}
    for r in records:
        for p in r["pruned"]:
            by_reason[p["kind"]][p["reason"]] += 1
            by_type[p["kind"]][p["reason"]][p["type"]] += 1
        if r["pruned"]:
            by_chunk[str(r["chunk_index"])] = dict(Counter(p["kind"] for p in r["pruned"]))
    raw, kept = Counter(), Counter()
    for r in records:
        raw.update(r["raw_counts"])
        kept.update(r["kept_counts"])
    stats = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "chunks": len(records),
        "before": {"entity_nodes": raw["entity_nodes"], "entity_relationships": raw["entity_relationships"]},
        "after": {"entity_nodes": kept["entity_nodes"], "entity_relationships": kept["entity_relationships"]},
        "totals": {kind: sum(c.values()) for kind, c in by_reason.items()},
        "by_reason": {kind: dict(c) for kind, c in by_reason.items()},
        "by_type": {kind: {reason: dict(c) for reason, c in rs.items()} for kind, rs in by_type.items()},
        "by_chunk": by_chunk,
    }
    schema_common.write_json(PRUNING_STATS_FILE, stats)
    log.info(f"[K3 가지치기] 엔티티 {stats['before']['entity_nodes']}→{stats['after']['entity_nodes']}개, "
             f"관계 {stats['before']['entity_relationships']}→{stats['after']['entity_relationships']}개 "
             f"(지운 항목: {stats['totals'] or '없음'})")
    return stats


# ---------------------------------------------------------------------------
# K4. 적재: 보관한 결과를 Neo4j에 씀(청크 노드, 엔티티 노드, 관계)
# ---------------------------------------------------------------------------
async def write_graph(driver, records: list[dict]) -> dict[str, int]:
    from neo4j_graphrag.components.kg_writer import Neo4jWriter
    from neo4j_graphrag.components.types import Neo4jGraph

    nodes, rels = [], []
    for r in records:
        g = Neo4jGraph.model_validate(r["graph"])
        nodes += g.nodes
        rels += g.relationships
    await Neo4jWriter(driver).run(Neo4jGraph(nodes=nodes, relationships=rels))
    written = {"nodes": len(nodes), "relationships": len(rels)}
    log.info(f"[K4 적재] 노드 {written['nodes']}개(청크 포함), 관계 {written['relationships']}개(FROM_CHUNK 포함)")
    return written


# ---------------------------------------------------------------------------
# K5. 병합: 같은 타입·같은 이름의 엔티티만 하나로 합침
# ---------------------------------------------------------------------------
def count_entities(driver) -> int:
    return driver.execute_query("MATCH (e:__Entity__) RETURN count(e) AS c").records[0]["c"]


async def merge_entities(driver) -> dict[str, int]:
    """SinglePropertyExactMatchResolver: __Entity__ 노드만 대상(청크 노드 제외), 라벨별로 name이 같은 노드를 합침."""
    from neo4j_graphrag.components.resolver import SinglePropertyExactMatchResolver

    before = count_entities(driver)
    await SinglePropertyExactMatchResolver(driver, resolve_property="name").run()
    after = count_entities(driver)
    log.info(f"[K5 병합] 엔티티 {before}개 → {after}개({before - after}개 합쳐짐)")
    return {"before": before, "after": after}


# ---------------------------------------------------------------------------
# K6. 엔티티 설명 임베딩 → entity_embedding 벡터 인덱스
# ---------------------------------------------------------------------------
def entity_text(row: dict[str, Any]) -> str:
    """`{타입}: {이름}. {설명 속성}` (설명 속성이 없으면 이름까지만)."""
    text = f"{row['type']}: {row['name']}."
    return f"{text} {row['description']}" if row.get("description") else text


def embed_entities(driver, embedder) -> dict[str, Any]:
    from neo4j_graphrag.indexes import create_vector_index, upsert_vectors

    rows = [r.data() for r in driver.execute_query(
        "MATCH (e:__Entity__) "
        "RETURN elementId(e) AS id, [l IN labels(e) WHERE NOT l IN $reserved][0] AS type, "
        "e.name AS name, e.description AS description",
        reserved=RESERVED_LABELS).records]
    if not rows:
        log.info("[K6 임베딩] 엔티티 없음")
        return {"entities": 0, "dimensions": 0}
    vectors = embed_texts(embedder, [entity_text(r) for r in rows])
    upsert_vectors(driver, ids=[r["id"] for r in rows], embedding_property="embedding", embeddings=vectors)
    dims = len(vectors[0])  # 차원은 실제 임베딩 길이에서 읽음
    create_vector_index(driver, ENTITY_INDEX, label="__Entity__", embedding_property="embedding",
                        dimensions=dims, similarity_fn="cosine")
    log.info(f"[K6 임베딩] 엔티티 {len(rows)}개 임베딩({dims}차원), 벡터 인덱스 `{ENTITY_INDEX}` 생성")
    return {"entities": len(rows), "dimensions": dims, "example": entity_text(rows[0])}


# ---------------------------------------------------------------------------
# K8. 커뮤니티 요약: GDS Leiden → 커뮤니티 노드·요약·임베딩 → community_embedding 벡터 인덱스
# ---------------------------------------------------------------------------
def detect_communities(driver) -> dict[str, int]:
    """엔티티와 엔티티 간 관계를 무방향 그래프로 투영해 Leiden(randomSeed 42)으로 묶음."""
    with driver.session() as s:
        s.run("CALL gds.graph.drop($g, false) YIELD graphName RETURN graphName", g=GDS_GRAPH).consume()
        s.run("MATCH (c:Community) DETACH DELETE c").consume()
        proj = s.run(
            "MATCH (src:__Entity__) OPTIONAL MATCH (src)-[r]->(tgt:__Entity__) "
            "WITH gds.graph.project($g, src, tgt, {}, {undirectedRelationshipTypes: ['*']}) AS g "
            "RETURN g.nodeCount AS nodes, g.relationshipCount AS rels", g=GDS_GRAPH).single()
        # 결과를 재현하기 위해 randomSeed를 고정하고 단일 스레드로 돌림
        s.run(
            "CALL gds.leiden.stream($g, {randomSeed: $seed, concurrency: 1}) YIELD nodeId, communityId "
            "WITH gds.util.asNode(nodeId) AS e, communityId "
            "MERGE (c:Community {id: communityId}) "
            "MERGE (e)-[:IN_COMMUNITY]->(c)", g=GDS_GRAPH, seed=LEIDEN_SEED).consume()
        s.run("MATCH (c:Community)<-[:IN_COMMUNITY]-(e) WITH c, count(e) AS n SET c.size = n").consume()
        s.run("CALL gds.graph.drop($g, false) YIELD graphName RETURN graphName", g=GDS_GRAPH).consume()
        sizes = s.run("MATCH (c:Community) RETURN count(c) AS total, "
                      "sum(CASE WHEN c.size >= 2 THEN 1 ELSE 0 END) AS multi, max(c.size) AS largest").single()
    stats = {"projected_nodes": proj["nodes"], "projected_relationships": proj["rels"],
             "communities": sizes["total"], "multi": sizes["multi"], "largest": sizes["largest"] or 0}
    log.info(f"[K8 커뮤니티] 투영 엔티티 {stats['projected_nodes']}개·관계 {stats['projected_relationships']}개 → "
             f"커뮤니티 {stats['communities']}개(엔티티 2개 이상 {stats['multi']}개, 최대 {stats['largest']}개)")
    return stats


SUMMARY_SYSTEM = "당신은 특허법 지식그래프를 교육생에게 설명하는 분석가입니다."
SUMMARY_PROMPT = """아래는 특허법 조문에서 뽑은 지식그래프의 한 커뮤니티(서로 촘촘히 이어진 엔티티 묶음)입니다.
이 묶음이 특허법의 어떤 내용을 다루는지 한국어 3문장 이내로 요약하세요.
- 엔티티와 관계에 없는 내용은 지어내지 마세요.
- 요약 문장만 출력하세요(제목, 목록, 따옴표 없이).

[엔티티]
{entities}

[관계]
{relationships}
"""
MAX_MEMBERS_IN_PROMPT = 60
MAX_RELS_IN_PROMPT = 80


def community_members(driver) -> list[dict[str, Any]]:
    """엔티티가 2개 이상인 커뮤니티별 구성 엔티티와 커뮤니티 안 관계."""
    records = driver.execute_query(
        "MATCH (c:Community) WHERE c.size >= 2 "
        "MATCH (e:__Entity__)-[:IN_COMMUNITY]->(c) "
        "WITH c, collect(e) AS members "
        "CALL (members) { "
        "  UNWIND members AS a MATCH (a)-[r]->(b:__Entity__) WHERE b IN members "
        "  RETURN collect(DISTINCT a.name + ' -' + type(r) + '-> ' + b.name) AS rels } "
        "RETURN c.id AS id, c.size AS size, "
        "[m IN members | [l IN labels(m) WHERE NOT l IN $reserved][0] + ': ' + m.name] AS entities, rels "
        "ORDER BY size DESC, id",
        reserved=RESERVED_LABELS).records
    return [r.data() for r in records]


def limit_sentences(text: str, n: int = 3) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(sentences[:n])


async def summarize_communities(driver, embedder, concurrency: int) -> dict[str, Any]:
    from neo4j_graphrag.exceptions import RateLimitError
    from neo4j_graphrag.indexes import create_vector_index

    communities = community_members(driver)
    llm = schema_common.make_llm(reasoning_effort="low", max_tokens=4000, json_mode=False)
    sem = asyncio.Semaphore(concurrency)
    summaries: dict[int, str] = {}
    failed: list[int] = []

    async def work(c: dict[str, Any]) -> None:
        """빈 응답·오류는 3회까지, Groq 호출 한도(429)는 1분씩 기다리며 5회까지 다시 시도함."""
        prompt = SUMMARY_PROMPT.format(
            entities="\n".join(f"- {e}" for e in sorted(c["entities"])[:MAX_MEMBERS_IN_PROMPT]),
            relationships="\n".join(f"- {r}" for r in sorted(c["rels"])[:MAX_RELS_IN_PROMPT]) or "- (없음)",
        )
        errors, waits = 0, 0
        async with sem:
            while errors < EXTRACT_RETRIES and waits <= RATE_LIMIT_RETRIES:
                try:
                    res = await llm.ainvoke(prompt, system_instruction=SUMMARY_SYSTEM)
                except RateLimitError:
                    waits += 1
                    await asyncio.sleep(RATE_LIMIT_WAIT_SEC)
                    continue
                except Exception:
                    errors += 1
                    continue
                text = limit_sentences(res.content or "")
                if text:
                    summaries[c["id"]] = text
                    return
                errors += 1
            failed.append(c["id"])

    await asyncio.gather(*(work(c) for c in communities))
    driver.execute_query(
        "UNWIND $rows AS row MATCH (c:Community {id: row.id}) SET c.summary = row.summary",
        rows=[{"id": k, "summary": v} for k, v in summaries.items()])

    ids = sorted(summaries)
    dims = 0
    if ids:
        vectors = embed_texts(embedder, [summaries[i] for i in ids])
        dims = len(vectors[0])
        driver.execute_query(
            "UNWIND $rows AS row MATCH (c:Community {id: row.id}) "
            "CALL db.create.setNodeVectorProperty(c, 'embedding', row.vec)",
            rows=[{"id": i, "vec": v} for i, v in zip(ids, vectors)])
        create_vector_index(driver, COMMUNITY_INDEX, label="Community", embedding_property="embedding",
                            dimensions=dims, similarity_fn="cosine")
    log.info(f"[K8 요약] 대상 커뮤니티 {len(communities)}개 중 요약 {len(summaries)}개(실패 {len(failed)}개), "
             f"요약 임베딩 {len(ids)}개({dims}차원), 벡터 인덱스 `{COMMUNITY_INDEX}` 생성")
    example = next(iter(sorted(communities, key=lambda c: -c["size"])), None)
    return {"targets": len(communities), "summarized": len(summaries), "failed": sorted(failed), "dimensions": dims,
            "example": {"id": example["id"], "size": example["size"], "summary": summaries.get(example["id"], "")} if example else None}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="특허법 GraphRAG 지식그래프 인덱스 구축(전체 색인)")
    p.add_argument("--limit", type=int, help="앞에서부터 N개 청크만 색인(시범 실행용, 예: --limit 5)")
    p.add_argument("--yes", action="store_true", help="기존 그래프 삭제 확인을 묻지 않음(확인을 받은 뒤에만 쓸 것)")
    p.add_argument("--from-extracted", action="store_true", help="K2·K3를 건너뛰고 extracted/의 보관본으로 적재부터 다시 함")
    p.add_argument("--resume", action="store_true", help="extracted/에 이미 있는 청크는 다시 추출하지 않음(중단된 실행 이어 하기)")
    p.add_argument("--concurrency", type=int, default=4, help="동시에 보낼 LLM 요청 수(기본 4)")
    p.add_argument("--skip-communities", action="store_true", help="K8 커뮤니티 요약을 건너뜀")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    setup_logging()
    started = time.time()
    mode = "보관본 적재" if args.from_extracted else ("시범 실행" if args.limit else "전체 색인")
    log.info(f"===== 특허법 KG 인덱스 구축 시작({mode}{f', --limit {args.limit}' if args.limit else ''}) =====")

    driver, schema = precheck()
    try:
        units = load_extraction_units(args.limit)                       # K1
        clear_graph(driver, args.yes)                                    # 그래프 비우기
        if args.from_extracted:
            records, failed = load_extracted(units)
        else:
            records, failed = await extract_and_prune(units, schema, args.concurrency, args.resume)  # K2·K3
        pruning = summarize_pruning(records)                             # K3 집계
        written = await write_graph(driver, records)                    # K4
        merge = await merge_entities(driver)                             # K5
        embedder = make_embedder()
        entity_emb = embed_entities(driver, embedder)                    # K6
        community = summary = None
        if not args.skip_communities:
            community = detect_communities(driver)                       # K8
            summary = await summarize_communities(driver, embedder, args.concurrency)

        run_info = {
            "mode": mode, "limit": args.limit, "units": len(units), "records": len(records), "failed": failed,
            "pruning": pruning, "written": written, "merge": merge, "entity_embedding": entity_emb,
            "community": community, "summary": summary, "elapsed_sec": round(time.time() - started),
            "model": schema_common.MODEL_NAME, "embedding_model": EMBEDDING_MODEL,
        }
        result = verify_graph.verify(driver, DESIGN_FILE)                # 적재 검증
        verify_graph.write_report(REPORT_FILE, run_info, result)
        verify_graph.log_summary(log, result)
        log.info(f"[보고서] {REPORT_FILE.relative_to(PROJECT_ROOT).as_posix()} 작성")
        log.info(f"===== 완료({run_info['elapsed_sec']}초): 청크 {len(records)}/{len(units)}건, 실패 {len(failed)}건, "
                 f"역량질문 {result['cq_pass']}/{len(result['cq'])} 조회됨 =====")
    finally:
        driver.close()


if __name__ == "__main__":
    asyncio.run(main())
