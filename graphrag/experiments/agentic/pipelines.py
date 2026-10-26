"""에이전틱 추출 방안(구성 요소와 조합).

팀 제안을 구성 요소로 나눠 조합함.
- base   : 원래 방식. LLMEntityRelationExtractor(기본 프롬프트) → GraphPruning (청크당 1회)
- cite   : 근거 조문 번호·원문 인용 구절을 함께 받아 추출하고, 인용이 원문에 없거나 조문 번호가 청크에 없으면 버림(최아이 E1, 강데이 1)
- scope  : 범위 판별 에이전트. 청크 안 조문마다 역량질문 유형에 답하는 내용인지 LLM이 판정하고, 범위 밖 조문에서 나온 항목을 버림(최아이 E3)
- vote   : 같은 추출을 3회 하고 정규화 이름 기준 2/3 이상 나온 항목만 남김(최아이 E2, 강데이 3)
- critic : 추출 결과를 스키마와 원문만 보고 검토해 근거 없는 항목을 버림(최아이 E4, 강데이 5). 역량질문·채점 규칙은 주지 않음(윤큐 누설 통제)

모든 방안은 마지막에 GraphPruning(스키마 밖 타입·관계·패턴·속성 제거)을 거침.
프롬프트에 개발용·검증용 청크 내용이나 기대 결과 문자열을 넣지 않음.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from harness import SCHEMA_DIR, Context  # noqa: F401  (SCHEMA_DIR: sys.path 설정을 위해 harness를 먼저 불러옴)
from common import load_competency_questions
from neo4j_graphrag.components.entity_relation_extractor import LLMEntityRelationExtractor
from neo4j_graphrag.components.graph_pruning import GraphPruning
from neo4j_graphrag.components.types import Neo4jGraph, Neo4jNode, Neo4jRelationship, TextChunk, TextChunks
from neo4j_graphrag.exceptions import LLMGenerationError
from neo4j_graphrag.generation.prompts import ERExtractionTemplate
from step7_fill_test import norm

RETRIES = 3
_ARTICLE = re.compile(r"(?m)^(제\s*\d+\s*조(?:의\s*\d+)?)(?=\s*\(|\s+삭제)")
_ARTICLE_REF = re.compile(r"제\s*\d+\s*조(?:의\s*\d+)?")
_TITLE = re.compile(r"(?m)^(제\s*\d+\s*조(?:의\s*\d+)?)\s*\(([^)]*)\)")


def norm_article(a: str) -> str:
    return re.sub(r"\s+", "", a)


# ---------------------------------------------------------------------------
# 청크의 조문 구성
# ---------------------------------------------------------------------------
def chunk_articles(ctx: Context, chunk_index: int) -> tuple[list[str], str | None]:
    """청크 안 조문 번호 목록과, 청크 첫 머리 앞 조각이 이어진 앞 조문(없으면 None)."""
    text = ctx.chunks[chunk_index]
    heads = list(_ARTICLE.finditer(text))
    arts = [norm_article(h.group(1)) for h in heads]
    lead = text[: heads[0].start()] if heads else text
    continued = None
    if lead.strip():
        prev = list(_ARTICLE.finditer(ctx.chunks.get(chunk_index - 1, "")))
        continued = norm_article(prev[-1].group(1)) if prev else None
    return ([continued] if continued else []) + arts, continued


def marked_text(ctx: Context, chunk_index: int) -> str:
    """앞 조문에서 이어진 조각이 있으면 그 조문 번호를 머리에 표시함(조문 번호를 붙일 수 있게)."""
    _, continued = chunk_articles(ctx, chunk_index)
    text = ctx.chunks[chunk_index]
    return f"[{continued}에서 이어지는 내용]\n{text}" if continued else text


# ---------------------------------------------------------------------------
# 공통: 추출 1회
# ---------------------------------------------------------------------------
CITE_RULES = """
Additional rules for this task (Korean statute text):
- For every node AND every relationship, add two properties:
  "article": the article number(s) the item is stated in, exactly as written in the text header, e.g. "제88조" or "제132조의17" (comma-separated if several)
  "evidence": the shortest verbatim span (copied exactly from the input text, max 60 characters, no ellipsis, no paraphrase) that states the item
- Only extract items that are explicitly stated in the input text.
"""


def cite_template() -> str:
    t = ERExtractionTemplate.DEFAULT_TEMPLATE
    return t.replace("Examples:", CITE_RULES + "\nExamples:")


async def extract_once(ctx: Context, unit: dict, template: str | None = None, text: str | None = None) -> Neo4jGraph:
    """LLMEntityRelationExtractor 1회(형식 오류 3회까지 재시도). 실패하면 빈 그래프와 실패 기록."""
    extra = {"prompt_template": template} if template else {}
    extractor = LLMEntityRelationExtractor(llm=ctx.json_llm, create_lexical_graph=False, **extra)
    chunk = TextChunk(text=text or unit["text"], index=unit["chunk_index"])
    for _ in range(RETRIES):
        try:
            async with ctx.sem:
                return await extractor.run(chunks=TextChunks(chunks=[chunk]), schema=ctx.schema)
        except LLMGenerationError:
            continue
    ctx.failures.append(f"{unit['chunk_index']}:extract")
    return Neo4jGraph()


async def prune(ctx: Context, graph: Neo4jGraph) -> Neo4jGraph:
    if not graph.nodes:
        return graph
    return (await GraphPruning().run(graph=graph, schema=ctx.schema)).graph


def strip_meta(graph: Neo4jGraph) -> Neo4jGraph:
    """근거 속성(article, evidence)을 지워 스키마 속성만 남김."""
    for n in graph.nodes:
        n.properties = {k: v for k, v in n.properties.items() if k not in ("article", "evidence")}
    for r in graph.relationships:
        r.properties = {k: v for k, v in r.properties.items() if k not in ("article", "evidence")}
    return graph


def keep(graph: Neo4jGraph, node_ok: set[str], rel_ok: list[Neo4jRelationship]) -> Neo4jGraph:
    """남길 관계의 양 끝 노드는 함께 남김(관계가 끊기지 않게)."""
    ends = {x for r in rel_ok for x in (r.start_node_id, r.end_node_id)}
    ids = node_ok | ends
    return Neo4jGraph(nodes=[n for n in graph.nodes if n.id in ids], relationships=rel_ok)


# ---------------------------------------------------------------------------
# 방안 구성 요소
# ---------------------------------------------------------------------------
def item_articles(props: dict) -> set[str]:
    return {norm_article(a) for a in _ARTICLE_REF.findall(str(props.get("article", "")))}


def evidence_ok(props: dict, chunk_norm: str) -> bool:
    """인용 구절이 원문에 있는지. LLM이 '…'나 쉼표로 떨어진 구절을 이어 붙이는 경우가 있어 조각마다 확인함."""
    parts = [norm(p) for p in re.split(r"\.\.\.|…|,|，", str(props.get("evidence", "")))]
    parts = [p for p in parts if len(p) >= 2]
    return bool(parts) and all(p in chunk_norm for p in parts)


def cite_filter(graph: Neo4jGraph, chunk_text: str, valid_articles: set[str], scope: set[str] | None) -> Neo4jGraph:
    """인용 구절이 원문에 있고, 조문 번호가 청크에 있는(그리고 범위가 있으면 범위 안인) 항목만 남김."""
    chunk_norm = norm(chunk_text)

    def ok(props: dict) -> bool:
        arts = item_articles(props) & valid_articles
        if not arts or not evidence_ok(props, chunk_norm):
            return False
        return scope is None or bool(arts & scope)

    ids = {n.id for n in graph.nodes}
    node_ok = {n.id for n in graph.nodes if ok(n.properties)}
    rel_ok = [r for r in graph.relationships if r.start_node_id in ids and r.end_node_id in ids and ok(r.properties)]
    return keep(graph, node_ok, rel_ok)


SCOPE_PROMPT = """[목표]
특허법 지식그래프 추출 범위를 정하기 위해, 아래 청크에 든 조문마다 역량질문 유형에 답하는 내용이 있는지 판정함

[역량질문(그래프가 답해야 하는 질문)]
{cqs}

[판정 기준]
- 범위 안(true): 위 역량질문과 같은 유형의 내용을 정한 조문. 유형은 특허 요건과 특허를 받을 수 없는 발명, 권리의 귀속, 심사 청구와 불복·분쟁 절차(청구 주체·기한·처리 기관·대상 결정), 권리의 존속기간, 권리의 효력 제한, 침해에 대한 민사 구제, 형벌임. 위 질문에 나온 조문이 아니어도 같은 유형이면 범위 안
- 범위 밖(false): 전자문서·서류 제출·송달·고유번호·열람·수수료·등록원부·특허증 같은 행정 사무, 기간 계산·재판관할 같은 일반 규정, 존속기간의 연장, 정의·목적 조항, 준용만 정한 조항
- 조문 번호는 아래 [조문 목록]에 있는 것만 씀

[조문 목록]
{articles}

[청크]
{text}

[출력]
JSON 하나만: {{"articles": [{{"article": "제N조", "in_scope": true, "reason": "한 줄"}}]}}
"""


SCOPE2_EXTRA = """
[스키마 관계(이 관계를 이루는 문장이 있는 조문은 범위 안)]
{relations}
- 위 관계 설명에 나온 문형(예: 기관이 어떤 절차를 관장·심리하도록 정한 조문, 절차를 청구할 수 있는 주체·기한을 정한 조문)이 있으면 범위 안
- 판단이 애매하면 범위 안(true)
"""


async def judge_scope(ctx: Context, unit: dict, with_schema: bool = False) -> set[str]:
    """범위 판별 에이전트(LLM 1회): 범위 안 조문 번호 집합. 실패하면 모든 조문을 범위 안으로 봄(재현율 보호).

    with_schema=True면 스키마 관계 설명을 판정 기준에 더함(2라운드). 기대 결과가 아닌 스키마에서 온 정보만 씀.
    """
    arts, _ = chunk_articles(ctx, unit["chunk_index"])
    if not arts:
        return set()
    titles = dict((norm_article(a), t) for a, t in _TITLE.findall(ctx.chunks[unit["chunk_index"]]))
    cqs = "\n".join(f"- {c['question']}" for c in load_competency_questions())
    prompt = SCOPE_PROMPT.format(cqs=cqs, articles="\n".join(f"- {a}({titles.get(a, '앞 청크에서 이어짐')})" for a in arts),
                                 text=marked_text(ctx, unit["chunk_index"]))
    if with_schema:
        rels = "\n".join(f"- {r['label']}: {r['description']}" for r in ctx.draft["schema"]["relationship_types"])
        prompt = prompt.replace("[조문 목록]", SCOPE2_EXTRA.format(relations=rels) + "\n[조문 목록]")
    for _ in range(RETRIES):
        try:
            async with ctx.sem:
                res = await ctx.json_llm.ainvoke(prompt)
            data = json.loads(res.content[res.content.find("{"): res.content.rfind("}") + 1])
            return {norm_article(a["article"]) for a in data.get("articles", []) if a.get("in_scope")} & set(arts)
        except Exception:
            continue
    ctx.failures.append(f"{unit['chunk_index']}:scope")
    return set(arts)


def item_keys(graph: Neo4jGraph) -> tuple[dict, dict]:
    """정규화 키 → 대표 노드/관계(투표용)."""
    by_id = {n.id: (n.label, norm(n.properties.get("name", ""))) for n in graph.nodes}
    nodes = {by_id[n.id]: n for n in graph.nodes}
    rels = {}
    for r in graph.relationships:
        if r.start_node_id in by_id and r.end_node_id in by_id:
            rels[(by_id[r.start_node_id], r.type, by_id[r.end_node_id])] = r
    return nodes, rels


def vote(graphs: list[Neo4jGraph], min_votes: int) -> Neo4jGraph:
    """정규화 키 기준 min_votes 이상 나온 노드·관계만 남김(관계의 양 끝은 함께 남김)."""
    node_cnt, rel_cnt, node_rep, rel_rep = Counter(), Counter(), {}, {}
    for g in graphs:
        nodes, rels = item_keys(g)
        node_cnt.update(nodes.keys())
        rel_cnt.update(rels.keys())
        for k, n in nodes.items():
            node_rep.setdefault(k, n)
        for k, r in rels.items():
            rel_rep.setdefault(k, r)
    keep_rels = [k for k, c in rel_cnt.items() if c >= min_votes]
    keep_nodes = {k for k, c in node_cnt.items() if c >= min_votes} | {x for s, _, o in keep_rels for x in (s, o)}
    ids = {k: f"v{i}" for i, k in enumerate(sorted(keep_nodes))}
    nodes = [Neo4jNode(id=ids[k], label=node_rep[k].label, properties=dict(node_rep[k].properties)) for k in sorted(keep_nodes)]
    rels = [Neo4jRelationship(start_node_id=ids[s], end_node_id=ids[o], type=t, properties={}) for s, t, o in keep_rels]
    return Neo4jGraph(nodes=nodes, relationships=rels)


CRITIC_PROMPT = """[목표]
LLM이 특허법 조문에서 뽑은 지식그래프 항목을 검토해, 원문과 스키마 규칙에 맞지 않는 항목을 골라냄

[스키마(노드 타입·관계 타입 설명, 추출 기준과 [뽑지 않음] 규칙 포함)]
{schema}

[원문]
{text}

[검토할 항목]
{items}

[판정 기준] 아래 중 하나라도 해당하면 drop
- 원문에 그 내용이 명시되어 있지 않음(추론·일반 지식으로 보탠 것)
- 스키마 설명의 [뽑지 않음] 규칙이나 관계 설명의 사용 조건에 어긋남
- 관계의 방향·타입이 원문 의미와 다름
그 밖의 항목은 keep. 확신이 없으면 keep

[출력]
JSON 하나만: {{"drop": [{{"id": "항목 id", "reason": "한 줄"}}]}}
"""


def schema_text(ctx: Context) -> str:
    s = ctx.draft["schema"]
    return json.dumps({"node_types": [{"label": n["label"], "description": n["description"]} for n in s["node_types"]],
                       "relationship_types": [{"label": r["label"], "description": r["description"]} for r in s["relationship_types"]]},
                      ensure_ascii=False, indent=1)


async def critic(ctx: Context, unit: dict, graph: Neo4jGraph) -> Neo4jGraph:
    """critic 에이전트(LLM 1회): drop 판정 항목을 버림. 실패하면 그대로 둠."""
    if not graph.nodes:
        return graph
    names = {n.id: f"{n.label}:{n.properties.get('name', '')}" for n in graph.nodes}
    lines = [f"- N{i} 노드 {names[n.id]}" for i, n in enumerate(graph.nodes)]
    lines += [f"- R{i} 관계 {names.get(r.start_node_id)} -{r.type}-> {names.get(r.end_node_id)}"
              for i, r in enumerate(graph.relationships)]
    prompt = CRITIC_PROMPT.format(schema=schema_text(ctx), text=ctx.chunks[unit["chunk_index"]], items="\n".join(lines))
    for _ in range(RETRIES):
        try:
            async with ctx.sem:
                res = await ctx.json_llm.ainvoke(prompt)
            data = json.loads(res.content[res.content.find("{"): res.content.rfind("}") + 1])
            drop = {str(d.get("id", "")).strip() for d in data.get("drop", [])}
            break
        except Exception:
            continue
    else:
        ctx.failures.append(f"{unit['chunk_index']}:critic")
        return graph
    node_ok = {n.id for i, n in enumerate(graph.nodes) if f"N{i}" not in drop}
    rel_ok = [r for i, r in enumerate(graph.relationships)
              if f"R{i}" not in drop and r.start_node_id in node_ok and r.end_node_id in node_ok]
    return Neo4jGraph(nodes=[n for n in graph.nodes if n.id in node_ok], relationships=rel_ok)


def drop_isolated(graph: Neo4jGraph) -> Neo4jGraph:
    """관계가 하나도 없는 노드를 버림(스키마 설명: 관계 없이 혼자 남는 노드는 출력하지 않음)."""
    ends = {x for r in graph.relationships for x in (r.start_node_id, r.end_node_id)}
    return Neo4jGraph(nodes=[n for n in graph.nodes if n.id in ends], relationships=graph.relationships)


# ---------------------------------------------------------------------------
# 방안(조합)
# ---------------------------------------------------------------------------
async def p_base(ctx: Context, unit: dict) -> Neo4jGraph:
    return await prune(ctx, await extract_once(ctx, unit))


async def _cite_graph(ctx: Context, unit: dict, scope: set[str] | None) -> Neo4jGraph:
    arts, _ = chunk_articles(ctx, unit["chunk_index"])
    g = await extract_once(ctx, unit, template=cite_template(), text=marked_text(ctx, unit["chunk_index"]))
    return cite_filter(g, ctx.chunks[unit["chunk_index"]], set(arts), scope)


async def p_cite(ctx: Context, unit: dict) -> Neo4jGraph:
    return await prune(ctx, strip_meta(await _cite_graph(ctx, unit, None)))


async def p_cite_scope(ctx: Context, unit: dict) -> Neo4jGraph:
    scope = await judge_scope(ctx, unit)
    return await prune(ctx, strip_meta(await _cite_graph(ctx, unit, scope)))


def segments(ctx: Context, chunk_index: int) -> list[tuple[str, str]]:
    """청크 본문을 (조문 번호, 본문) 조각으로 나눔. 첫 머리 앞 조각은 앞 조문에서 이어진 것."""
    text = ctx.chunks[chunk_index]
    heads = list(_ARTICLE.finditer(text))
    _, continued = chunk_articles(ctx, chunk_index)
    out = []
    lead = text[: heads[0].start()] if heads else text
    if lead.strip():
        out.append((continued or "알 수 없음", lead))
    for i, h in enumerate(heads):
        out.append((norm_article(h.group(1)), text[h.start(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]))
    return out


SCOPE_MARK = "[참고 문맥: 아래 {art}는 추출 대상이 아님. 이 부분에서는 노드·관계를 뽑지 말 것]\n{body}\n[참고 문맥 끝]\n"


async def _scoped_text(ctx: Context, unit: dict, mode: str, with_schema: bool = False) -> str:
    scope = await judge_scope(ctx, unit, with_schema)
    parts = []
    for art, body in segments(ctx, unit["chunk_index"]):
        if art in scope:
            parts.append(body)
        elif mode == "mask":
            parts.append(SCOPE_MARK.format(art=art, body=body.strip()))
    return "\n".join(parts).strip()


async def p_scope_mask(ctx: Context, unit: dict) -> Neo4jGraph:
    """범위 판별 에이전트 → 범위 밖 조문은 '참고 문맥, 추출 금지'로 표시해 추출(문맥 유지, 청크당 2회)."""
    text = await _scoped_text(ctx, unit, "mask")
    return await prune(ctx, await extract_once(ctx, unit, text=text))


async def p_scope_cut(ctx: Context, unit: dict) -> Neo4jGraph:
    """범위 판별 에이전트 → 범위 밖 조문을 지우고 추출. 범위 안 조문이 없으면 추출하지 않음(청크당 1 ~ 2회)."""
    text = await _scoped_text(ctx, unit, "cut")
    if not text:
        return Neo4jGraph()
    return await prune(ctx, await extract_once(ctx, unit, text=text))


async def p_scope2_mask(ctx: Context, unit: dict) -> Neo4jGraph:
    """2라운드: 스키마 관계 설명을 더한 범위 판별 → 범위 밖 조문 표시 → 추출(청크당 2회)."""
    text = await _scoped_text(ctx, unit, "mask", with_schema=True)
    return await prune(ctx, await extract_once(ctx, unit, text=text))


async def p_scope2_mask_critic(ctx: Context, unit: dict) -> Neo4jGraph:
    """2라운드: scope2_mask 결과를 critic이 검토(청크당 3회)."""
    return await prune(ctx, await critic(ctx, unit, await p_scope2_mask(ctx, unit)))


async def p_scope2_mask_vote(ctx: Context, unit: dict) -> Neo4jGraph:
    """2라운드: 범위 판별 1회 + 표시한 본문으로 3회 추출해 2/3 이상 항목만(청크당 4회)."""
    import asyncio

    text = await _scoped_text(ctx, unit, "mask", with_schema=True)
    graphs = await asyncio.gather(*(extract_once(ctx, unit, text=text) for _ in range(3)))
    pruned = [await prune(ctx, g) for g in graphs]
    return vote(pruned, 2)


def title_names(ctx: Context, graph: Neo4jGraph, chunk_index: int) -> Neo4jGraph:
    """스키마 이름 규칙('조문 제목에 절차 이름이 있으면 그 이름을 씀')을 규칙으로 적용(LLM 호출 없음).

    Procedure 이름이 청크 안 조문 제목 하나의 끝부분과 같고 제목보다 짧으면, 그 제목 전체로 바꿈.
    예: '심판' → '○○에 대한 심판'(제목이 '…심판'으로 끝나는 조문이 그 청크에 하나뿐일 때만)
    """
    titles = [t.strip() for _, t in _TITLE.findall(ctx.chunks[chunk_index])]
    for n in graph.nodes:
        if n.label != "Procedure":
            continue
        name = str(n.properties.get("name", "")).strip()
        cands = [t for t in titles if norm(t).endswith(norm(name)) and len(norm(t)) > len(norm(name))]
        if name and len(cands) == 1:
            n.properties = {**n.properties, "name": cands[0]}
    return graph


GLEAN_PROMPT = """[목표]
특허법 원문에서 이미 뽑은 지식그래프 항목을 보고, 원문에 근거가 있는데 빠진 관계만 더 찾음

[스키마 관계(패턴과 사용 조건)]
{relations}

[원문]
{text}

[이미 뽑은 항목]
{items}

[처리]
- 위 관계 설명의 조건에 맞고 원문에 명시된 관계 중 [이미 뽑은 항목]에 없는 것만 씀
- 노드 이름은 원문 표현 그대로(이미 뽑은 노드와 같은 대상이면 그 이름을 그대로 씀)
- [참고 문맥]으로 표시된 부분에서만 나오는 관계는 쓰지 않음. 다만 표시 안 된 부분의 노드와 참고 문맥의 기관을 잇는 HANDLED_BY처럼 관계 설명이 허용한 경우는 씀
- 빠진 것이 없으면 빈 목록

[출력]
JSON 하나만: {{"relationships": [{{"start": {{"label": "노드 타입", "name": "이름"}}, "type": "관계 타입", "end": {{"label": "노드 타입", "name": "이름"}}}}]}}
"""


async def glean(ctx: Context, unit: dict, graph: Neo4jGraph, text: str, existing_only: bool = False) -> Neo4jGraph:
    """패턴 보충 에이전트(LLM 1회): 빠진 관계를 더함. 새 노드는 이름으로 기존 노드와 맞춤. 실패하면 그대로 둠.

    existing_only=True면 이미 뽑힌 노드끼리 잇는 관계만 받음(새 노드를 만들지 않아 지어냄을 막음).
    """
    names = {n.id: f"{n.label}:{n.properties.get('name', '')}" for n in graph.nodes}
    items = [f"- 노드 {v}" for v in names.values()]
    items += [f"- 관계 {names.get(r.start_node_id)} -{r.type}-> {names.get(r.end_node_id)}" for r in graph.relationships]
    rels = "\n".join(f"- ({p['source']})-[{p['relationship']}]->({p['target']}): "
                     + next(r["description"] for r in ctx.draft["schema"]["relationship_types"] if r["label"] == p["relationship"])
                     for p in ctx.draft["schema"]["patterns"])
    prompt = GLEAN_PROMPT.format(relations=rels, text=text, items="\n".join(items) or "- (없음)")
    for _ in range(RETRIES):
        try:
            async with ctx.sem:
                res = await ctx.json_llm.ainvoke(prompt)
            data = json.loads(res.content[res.content.find("{"): res.content.rfind("}") + 1])
            added = [r for r in data.get("relationships", []) if isinstance(r, dict)]
            break
        except Exception:
            continue
    else:
        ctx.failures.append(f"{unit['chunk_index']}:glean")
        return graph
    key = {(n.label, norm(n.properties.get("name", ""))): n.id for n in graph.nodes}
    nodes, rels_out = list(graph.nodes), list(graph.relationships)
    have = {(r.start_node_id, r.type, r.end_node_id) for r in rels_out}

    def node_id(end: dict) -> str | None:
        label, name = str(end.get("label", "")), str(end.get("name", "")).strip()
        if not label or not name:
            return None
        k = (label, norm(name))
        if k not in key:
            if existing_only:
                return None
            key[k] = f"g{len(key)}"
            nodes.append(Neo4jNode(id=key[k], label=label, properties={"name": name}))
        return key[k]

    for r in added:
        s, e = node_id(r.get("start", {})), node_id(r.get("end", {}))
        t = str(r.get("type", ""))
        if s and e and t and (s, t, e) not in have:
            rels_out.append(Neo4jRelationship(start_node_id=s, end_node_id=e, type=t))
            have.add((s, t, e))
    return Neo4jGraph(nodes=nodes, relationships=rels_out)


async def p_final(ctx: Context, unit: dict) -> Neo4jGraph:
    """3라운드 F1: scope2_mask → 제목 이름 규칙 → 고립 노드 제거(청크당 2회)."""
    g = await p_scope2_mask(ctx, unit)
    return drop_isolated(title_names(ctx, g, unit["chunk_index"]))


async def p_final_glean(ctx: Context, unit: dict) -> Neo4jGraph:
    """3라운드 F2: 범위 판별 → 표시 본문 추출 → 패턴 보충 → 제목 이름 규칙 → 가지치기 → 고립 노드 제거(청크당 3회)."""
    text = await _scoped_text(ctx, unit, "mask", with_schema=True)
    g = await prune(ctx, await extract_once(ctx, unit, text=text))
    g = await glean(ctx, unit, g, text)
    return drop_isolated(await prune(ctx, title_names(ctx, g, unit["chunk_index"])))


async def p_final_link(ctx: Context, unit: dict) -> Neo4jGraph:
    """3라운드 F4: 범위 판별 → 표시 본문 추출 → 제목 이름 규칙 → 기존 노드끼리만 관계 보충 → 가지치기 → 고립 노드 제거(청크당 3회)."""
    text = await _scoped_text(ctx, unit, "mask", with_schema=True)
    g = title_names(ctx, await prune(ctx, await extract_once(ctx, unit, text=text)), unit["chunk_index"])
    g = await glean(ctx, unit, g, text, existing_only=True)
    return drop_isolated(await prune(ctx, g))


async def p_final_vote(ctx: Context, unit: dict) -> Neo4jGraph:
    """3라운드 F3: scope2_mask_vote → 제목 이름 규칙 → 고립 노드 제거(청크당 4회)."""
    g = await p_scope2_mask_vote(ctx, unit)
    return drop_isolated(title_names(ctx, g, unit["chunk_index"]))


async def p_vote(ctx: Context, unit: dict) -> Neo4jGraph:
    import asyncio

    graphs = await asyncio.gather(*(p_base(ctx, unit) for _ in range(3)))
    return vote(list(graphs), 2)


async def p_critic(ctx: Context, unit: dict) -> Neo4jGraph:
    return await prune(ctx, await critic(ctx, unit, await prune(ctx, await extract_once(ctx, unit))))


PIPELINES: dict[str, Any] = {
    "base": p_base,
    "cite": p_cite,
    "cite_scope": p_cite_scope,
    "vote": p_vote,
    "critic": p_critic,
    "scope_mask": p_scope_mask,
    "scope_cut": p_scope_cut,
    "scope2_mask": p_scope2_mask,
    "scope2_mask_critic": p_scope2_mask_critic,
    "scope2_mask_vote": p_scope2_mask_vote,
    "final": p_final,
    "final_glean": p_final_glean,
    "final_vote": p_final_vote,
    "final_link": p_final_link,
}
