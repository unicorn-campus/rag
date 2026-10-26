"""적재 검증과 색인 보고서(index-report.md) 작성.

- 노드 타입별·관계 타입별 개수를 셈
- 역량질문마다 schema-design.md 5절의 패턴 경로를 Cypher로 조회해 1건 이상 나오는지 확인함
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

# 역량질문별 패턴 경로 조회. schema-design.md 5절의 패턴 경로를 그대로 Cypher MATCH로 옮김.
# - match: 패턴 경로(판정 기준: 부분마다 1건 이상이면 통과)
# - anchor: 질문 속 대상으로 좁힌 조건(참고용 건수, 판정에는 쓰지 않음)
# - ret: 예시로 보여 줄 열
CQ_QUERIES: dict[str, list[dict[str, str]]] = {
    "CQ01": [
        {"match": "MATCH (i:Invention)-[:ELIGIBLE_FOR]->(r:Right)", "anchor": "r.name = '특허'",
         "ret": "i.name AS 발명, r.name AS 권리"},
        {"match": "MATCH (i:Invention)-[:NOT_ELIGIBLE_FOR]->(r:Right)", "anchor": "r.name = '특허'",
         "ret": "i.name AS 발명, r.name AS 권리"},
    ],
    "CQ02": [
        {"match": "MATCH (i:Invention)-[:NOT_ELIGIBLE_FOR]->(r:Right)", "anchor": "r.name = '특허'",
         "ret": "i.name AS 발명, r.name AS 권리"},
    ],
    "CQ03": [
        {"match": "MATCH (a:Actor)-[:CAN_FILE]->(p:Procedure)", "anchor": "p.name CONTAINS '심사'",
         "ret": "a.name AS 주체, p.name AS 절차"},
    ],
    "CQ04": [
        {"match": "MATCH (a:Actor)-[:CAN_FILE]->(p:Procedure)-[:TARGETS]->(d:Decision), (p)-[:HANDLED_BY]->(h:Actor)",
         "anchor": "d.name CONTAINS '거절결정'",
         "ret": "a.name AS 주체, p.name AS 절차, d.name AS 대상결정, h.name AS 처리기관"},
    ],
    "CQ05": [
        {"match": "MATCH (p:Procedure)-[:HAS_DEADLINE]->(t:Period)", "anchor": "p.name CONTAINS '심사'",
         "ret": "p.name AS 절차, t.name AS 기한"},
    ],
    "CQ06": [
        {"match": "MATCH (r:Right)-[:HAS_DURATION]->(t:Period)", "anchor": "r.name = '특허권'",
         "ret": "r.name AS 권리, t.name AS 존속기간"},
    ],
    "CQ07": [
        {"match": "MATCH (a:Actor)-[:PUNISHED_BY]->(p:Penalty)", "anchor": "a.name CONTAINS '침해'",
         "ret": "a.name AS 주체, p.name AS 형벌"},
    ],
    "CQ08": [
        {"match": "MATCH (r:Right)-[:NOT_EFFECTIVE_FOR]->(x:Exclusion)", "anchor": "r.name = '특허권'",
         "ret": "r.name AS 권리, x.name AS 효력제외"},
    ],
    "CQ09": [
        {"match": "MATCH (a:Actor)-[:CAN_CLAIM]->(m:Remedy)", "anchor": "a.name CONTAINS '특허권자'",
         "ret": "a.name AS 주체, m.name AS 구제수단"},
    ],
}

_CQ_ROW = re.compile(r"^\|\s*(CQ\d+)\s*\|\s*([^|]+?)\s*\|\s*`([^`]+)`\s*\|", re.M)


def load_cq_paths(design_file: Path) -> list[dict[str, str]]:
    """schema-design.md 5절 표에서 역량질문 ID, 질문, 패턴 경로를 읽음."""
    text = design_file.read_text(encoding="utf-8")
    rows = [{"id": i, "question": q, "path": p} for i, q, p in _CQ_ROW.findall(text)]
    missing = [r["id"] for r in rows if r["id"] not in CQ_QUERIES]
    if missing:
        raise SystemExit(f"[중단] {', '.join(missing)}의 조회 Cypher가 verify_graph.CQ_QUERIES에 없음")
    return rows


def _count(driver, query: str) -> int:
    return driver.execute_query(query).records[0]["c"]


def check_cq(driver, cq: dict[str, str]) -> dict[str, Any]:
    parts = []
    for q in CQ_QUERIES[cq["id"]]:
        where = f" WHERE {q['anchor']}" if q.get("anchor") else ""
        count = _count(driver, f"{q['match']} RETURN count(*) AS c")
        anchored = _count(driver, f"{q['match']}{where} RETURN count(*) AS c") if where else count
        # 예시는 질문 대상으로 좁힌 결과를 먼저 보여 주고, 없으면 패턴 경로 전체에서 보여 줌
        sample_where = where if anchored else ""
        samples = [r.data() for r in driver.execute_query(
            f"{q['match']}{sample_where} RETURN DISTINCT {q['ret']} LIMIT 3").records]
        parts.append({"match": q["match"], "anchor": q.get("anchor", ""), "count": count,
                      "anchored": anchored, "samples": samples})
    return {**cq, "parts": parts, "passed": all(p["count"] >= 1 for p in parts)}


def verify(driver, design_file: Path) -> dict[str, Any]:
    """노드·관계 타입별 개수, 고립 엔티티, 벡터 인덱스, 역량질문별 조회 결과."""
    nodes = {r["l"]: r["c"] for r in driver.execute_query(
        "MATCH (n) UNWIND labels(n) AS l WITH l WHERE l <> '__KGBuilder__' "
        "RETURN l, count(*) AS c ORDER BY c DESC").records}
    rels = {r["t"]: r["c"] for r in driver.execute_query(
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC").records}
    isolated = _count(driver, "MATCH (e:__Entity__) WHERE NOT EXISTS { (e)--(:__Entity__) } RETURN count(e) AS c")
    no_chunk = _count(driver, "MATCH (e:__Entity__) WHERE NOT EXISTS { (e)-[:FROM_CHUNK]->(:Chunk) } RETURN count(e) AS c")
    indexes = [r.data() for r in driver.execute_query(
        "SHOW VECTOR INDEXES YIELD name, labelsOrTypes, properties, state, options "
        "RETURN name, labelsOrTypes[0] AS label, properties[0] AS property, state, "
        "options.indexConfig['vector.dimensions'] AS dimensions").records]
    embedded = {
        "entity": _count(driver, "MATCH (e:__Entity__) WHERE e.embedding IS NOT NULL RETURN count(e) AS c"),
        "community": _count(driver, "MATCH (c:Community) WHERE c.embedding IS NOT NULL RETURN count(c) AS c"),
    }
    cq = [check_cq(driver, c) for c in load_cq_paths(design_file)]
    return {"nodes": nodes, "relationships": rels, "isolated_entities": isolated, "entities_without_chunk": no_chunk,
            "vector_indexes": indexes, "embedded": embedded, "cq": cq, "cq_pass": sum(c["passed"] for c in cq)}


def log_summary(log, result: dict[str, Any]) -> None:
    log.info("[적재 검증] 노드: " + ", ".join(f"{k} {v}" for k, v in result["nodes"].items()))
    log.info("[적재 검증] 관계: " + ", ".join(f"{k} {v}" for k, v in result["relationships"].items()))
    log.info(f"[적재 검증] 고립 엔티티(엔티티 간 관계 없음) {result['isolated_entities']}개")
    for c in result["cq"]:
        counts = " / ".join(f"{p['count']}건(질문 대상 {p['anchored']}건)" for p in c["parts"])
        log.info(f"  {c['id']} {'O' if c['passed'] else 'X'} {counts} — {c['question']}")


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------
def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return out


def _sample_text(samples: list[dict[str, Any]]) -> str:
    return "<br>".join(" → ".join(str(v) for v in s.values()) for s in samples) or "-"


def write_report(path: Path, run: dict[str, Any], result: dict[str, Any]) -> None:
    pr = run["pruning"]
    lines = [
        "# 특허법 GraphRAG 색인 보고서",
        "",
        f"- 작성: {datetime.now():%Y-%m-%d %H:%M}, 실행 방식: {run['mode']}"
        + (f"(--limit {run['limit']})" if run["limit"] else "") + f", 소요 {run['elapsed_sec']}초",
        f"- 추출 LLM: Groq `{run['model']}`(추출 프롬프트는 neo4j-graphrag 기본값), 임베딩: Cohere `{run['embedding_model']}`",
        "- 스키마: `graphrag/schema/schema.json`(노드 9·관계 12·패턴 12, 스키마 밖 타입·관계·패턴 불허)",
        "- 용어: 엔티티(entity)는 조문에서 뽑은 개념 노드, 청크(chunk)는 벡터DB에 저장한 본문 조각, "
        "커뮤니티(community)는 서로 촘촘히 이어진 엔티티 묶음",
        "",
        "## 1. 단계별 건수",
        "",
    ]
    ent_emb = run["entity_embedding"]
    com, summ = run.get("community"), run.get("summary")
    steps = [
        ["K1 추출 단위", f"벡터DB 청크 {run['units']}건(새로 자르지 않음)"],
        ["K2 추출", f"성공 {run['records']}건, 실패 {len(run['failed'])}건 / 추출 원본 엔티티 {pr['before']['entity_nodes']}개·관계 {pr['before']['entity_relationships']}개"],
        ["K3 가지치기", f"엔티티 {pr['before']['entity_nodes']}→{pr['after']['entity_nodes']}개, 관계 {pr['before']['entity_relationships']}→{pr['after']['entity_relationships']}개"],
        ["K4 적재", f"노드 {run['written']['nodes']}개(청크 노드 포함), 관계 {run['written']['relationships']}개(FROM_CHUNK 포함)"],
        ["K5 병합", f"엔티티 {run['merge']['before']}→{run['merge']['after']}개({run['merge']['before'] - run['merge']['after']}개 합쳐짐)"],
        ["K6 엔티티 임베딩", f"{ent_emb['entities']}개, {ent_emb['dimensions']}차원, 인덱스 `entity_embedding`"],
        ["K7 청크 정리", "생략(전체 색인)"],
    ]
    if com and summ:
        steps.append(["K8 커뮤니티", f"커뮤니티 {com['communities']}개(엔티티 2개 이상 {com['multi']}개, 최대 {com['largest']}개)"])
        steps.append(["K8 요약", f"요약 {summ['summarized']}/{summ['targets']}개(실패 {len(summ['failed'])}개), 임베딩 {summ['dimensions']}차원, 인덱스 `community_embedding`"])
    else:
        steps.append(["K8 커뮤니티", "건너뜀(--skip-communities)"])
    lines += _table(["단계", "결과"], steps)
    if ent_emb.get("example"):
        lines += ["", f"- 엔티티 설명 문장 예: `{ent_emb['example']}` (스키마에 설명 속성이 없어 이름까지만 씀)"]
    if summ and summ.get("example"):
        ex = summ["example"]
        lines += [f"- 가장 큰 커뮤니티(id {ex['id']}, 엔티티 {ex['size']}개) 요약: {ex['summary'] or '(없음)'}"]

    lines += ["", "## 2. 실패 청크", ""]
    if run["failed"]:
        lines += _table(["chunk_index", "사유"], [[f["chunk_index"], f["error"]] for f in run["failed"]])
    else:
        lines.append("- 없음")

    lines += ["", "## 3. 가지치기로 지워진 항목", "",
              "- GraphPruning이 스키마에 없는 노드·관계·속성을 지움(`additional_*` 모두 False). 상세: `pruning-stats.json`", ""]
    rows = []
    for kind, reasons in pr["by_type"].items():
        for reason, types in reasons.items():
            rows.append([kind, reason, sum(types.values()), ", ".join(f"{t} {n}" for t, n in sorted(types.items(), key=lambda x: -x[1]))])
    lines += _table(["종류", "사유", "건수", "타입별"], rows) if rows else ["- 없음"]

    lines += ["", "## 4. 병합 전후 엔티티 수", "",
              f"- 같은 타입·같은 이름(`name`)의 엔티티만 합침(SinglePropertyExactMatchResolver). 청크 노드는 대상 아님",
              f"- 병합 전 {run['merge']['before']}개 → 병합 후 {run['merge']['after']}개",
              "", "## 5. 노드 타입별·관계 타입별 개수", ""]
    lines += _table(["노드 라벨", "개수"], [[k, v] for k, v in result["nodes"].items()])
    lines += [""]
    lines += _table(["관계 타입", "개수"], [[k, v] for k, v in result["relationships"].items()])
    lines += ["",
              f"- 고립 엔티티(다른 엔티티와 관계가 없고 청크와만 이어진 노드): {result['isolated_entities']}개",
              f"- 출처 청크가 없는 엔티티: {result['entities_without_chunk']}개",
              f"- 임베딩이 들어간 노드: 엔티티 {result['embedded']['entity']}개, 커뮤니티 {result['embedded']['community']}개",
              "", "벡터 인덱스:", ""]
    lines += _table(["이름", "라벨", "속성", "차원", "상태"],
                    [[i["name"], i["label"], i["property"], i["dimensions"], i["state"]] for i in result["vector_indexes"]])

    lines += ["", "## 6. 역량질문별 조회 결과", "",
              "- 판정: `schema-design.md` 5절의 패턴 경로를 Cypher로 조회해 1건 이상이면 O(경로가 여러 부분이면 모두 1건 이상)",
              "- 질문 대상 건수: 질문 속 대상(예: '특허권', '거절결정')으로 좁힌 참고 건수(판정에 쓰지 않음)", ""]
    rows = []
    for c in result["cq"]:
        rows.append([c["id"], c["question"], f"`{c['path']}`",
                     " / ".join(str(p["count"]) for p in c["parts"]),
                     " / ".join(str(p["anchored"]) for p in c["parts"]),
                     "O" if c["passed"] else "X",
                     "<br>".join(_sample_text(p["samples"]) for p in c["parts"])])
    lines += _table(["ID", "역량질문", "패턴 경로", "조회 건수", "질문 대상 건수", "판정", "예시(최대 3건)"], rows)
    lines += ["", f"- 결과: {result['cq_pass']}/{len(result['cq'])} 역량질문의 패턴 경로가 1건 이상 조회됨", ""]

    lines += ["## 7. 남은 한계", ""]
    if run["failed"]:
        lines.append(f"- 실패 청크 {len(run['failed'])}건은 그래프에 없음. `--resume`으로 실패 청크만 다시 추출할 수 있음")
    lines += [
        f"- 지어냄: 스키마 채우기 시험의 지어냄 비율(뽑힌 항목 중 기대에 없는 비율)이 0.294였고 같은 추출 조건을 씀. "
        f"고립 엔티티 {result['isolated_entities']}개가 남아 있음(사용자 결정으로 고립 노드 제거 후처리는 넣지 않음)",
        "- 병합: 이름이 글자까지 같은 엔티티만 합침. 조사·띄어쓰기·'또는' 묶음 차이로 같은 개념이 여러 노드로 남을 수 있음",
        "- 실행 변동: 추출 LLM은 temperature 0에서도 실행마다 결과가 달라, 다시 색인하면 노드·관계 수가 달라짐",
        "- 질문 대상 건수가 0인 역량질문은 패턴 경로는 있어도 질문이 묻는 대상(예: 거절결정 불복 심판)이 이어지지 않은 것임",
        "- 엔티티 설명 속성이 스키마에 없어 엔티티 임베딩은 `{타입}: {이름}.`만 담음",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
