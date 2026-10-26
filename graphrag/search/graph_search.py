"""특허법 GraphRAG 검색 예제.

질문마다 같은 순서로 도는 고정 워크플로우:
    S1 라우팅(TypeSafe Jev) → S2 검색(Local / Global / Hybrid) → S3 답변 생성(Groq gpt-oss-120b)
질문 1건당 모델 호출은 S1 Jev 1회, S3 Groq 1회로 고정함(임베딩 호출은 제외).
그래프는 읽기만 함. API Key와 Neo4j 비밀번호 값은 화면·로그·파일에 남기지 않음.

실행 예:
    python graphrag/search/graph_search.py "특허권의 존속기간은 언제까지인가?"
    python graphrag/search/graph_search.py "특허법은 어떤 흐름으로 권리를 보호하는가?" --verbose
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SEARCH_DIR = Path(__file__).resolve().parent           # rag/graphrag/search
sys.path.insert(0, str(SEARCH_DIR.parent / "index"))   # 색인 단계 설정(접속, 인덱스 이름, 임베딩 모델) 재사용

import index_graph  # noqa: E402  (graphrag/index/index_graph.py, 이 안에서 graphrag/schema/common.py도 불러옴)
import common as schema_common  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
ROUTER_MODEL = "jev-latest"
ROUTES = ("local", "global", "hybrid")
FALLBACK_ROUTE = "hybrid"                  # Jev 호출이 실패하면 쓰는 검색 방식
ROUTE_INSTRUCTIONS = "이 질문에 답하려면 어떤 그래프 검색 방식이 맞는지 고르시오"
# 선택지 설명은 검색 방식 3가지 설명을 그대로 씀
ROUTE_CRITERIA = {
    "local": "질문과 가까운 엔티티를 찾고, 그 주변 관계와 출처 청크를 근거로 씀. 특정 개념, 요건, 기간을 묻는 질문용",
    "global": "질문과 가까운 커뮤니티 요약을 근거로 씀. 법 전체의 주제나 흐름을 묻는 질문용",
    "hybrid": "Local과 Global 결과를 함께 근거로 씀. 특정 대상을 전체 맥락 속에서 묻는 질문용",
}

LOCAL_TOP_K = 5                            # 엔티티 상위 5개
GLOBAL_TOP_K = 3                           # 커뮤니티 요약 상위 3개
MAX_RELATIONS_PER_ENTITY = 20
MAX_CHUNKS_PER_ENTITY = 2
NO_EVIDENCE = "그래프에서 근거를 찾지 못함"

# Local: 벡터 검색으로 찾은 엔티티(node, score)의 1단계 이웃 관계와 출처 청크 본문을 함께 가져옴.
# 출처 청크는 그 엔티티의 이웃 엔티티가 많이 함께 나온 청크(관계가 뽑힌 청크)를 먼저 고름
LOCAL_RETRIEVAL_QUERY = f"""
RETURN [l IN labels(node) WHERE NOT l IN $reserved][0] AS type,
       node.name AS name,
       score,
       collect {{
         MATCH (node)-[r]-(o:__Entity__)
         RETURN CASE WHEN startNode(r) = node
                     THEN node.name + ' -' + type(r) + '-> ' + o.name
                     ELSE o.name + ' -' + type(r) + '-> ' + node.name END
         LIMIT {MAX_RELATIONS_PER_ENTITY}
       }} AS relations,
       collect {{
         MATCH (node)-[:FROM_CHUNK]->(c:Chunk)
         WITH c, COUNT {{ (c)<-[:FROM_CHUNK]-(:__Entity__)--(node) }} AS overlap
         ORDER BY overlap DESC, c.index
         RETURN {{chunk_index: c.index, text: c.text}}
         LIMIT {MAX_CHUNKS_PER_ENTITY}
       }} AS chunks
"""


@dataclass
class Route:
    choice: str
    confidence: float | None
    probabilities: dict[str, float] = field(default_factory=dict)
    fallback: bool = False                 # Jev 호출 실패로 hybrid를 쓴 경우
    error: str = ""


@dataclass
class SearchResult:
    question: str
    route: Route
    executed: list[str]                    # 실제로 실행한 검색(local, global)
    local: list[dict[str, Any]]
    global_: list[dict[str, Any]]
    context: str
    answer: str


# ---------------------------------------------------------------------------
# 사전 검사
# ---------------------------------------------------------------------------
def precheck():
    """Neo4j 접속, 벡터 인덱스 2개, API Key 3개를 확인함. 안 되면 안내 후 종료. Returns: 드라이버."""
    import neo4j

    load_dotenv(schema_common.ENV_FILE)
    uri, password = index_graph.neo4j_settings()
    if not password:
        sys.exit("[사전 검사 실패] NEO4J_PASSWORD가 비어 있음 → `neo4j/.env`에 NEO4J_PASSWORD를 넣을 것(neo4j/README.md 참고)")
    driver = neo4j.GraphDatabase.driver(uri, auth=(index_graph.NEO4J_USER, password), notifications_min_severity="OFF")
    try:
        driver.verify_connectivity()
        names = {r["name"] for r in driver.execute_query("SHOW VECTOR INDEXES YIELD name RETURN name").records}
    except Exception as e:
        driver.close()
        sys.exit(
            f"[사전 검사 실패] Neo4j({uri}) 접속 실패({type(e).__name__}).\n"
            "  → `neo4j/README.md`대로 컨테이너를 띄울 것: docker compose -f neo4j/compose.yml up -d"
        )
    missing = [i for i in (index_graph.ENTITY_INDEX, index_graph.COMMUNITY_INDEX) if i not in names]
    if missing:
        driver.close()
        sys.exit(f"[사전 검사 실패] 벡터 인덱스 없음: {', '.join(missing)} → `prompts/graphrag_index.md`를 먼저 수행할 것")
    empty = [k for k in ("GROQ_API_KEY", "COHERE_API_KEY", "JEV_API_KEY") if not os.getenv(k, "").strip()]
    if empty:
        driver.close()
        sys.exit(f"[사전 검사 실패] `.env`에 비어 있는 키: {', '.join(empty)}")
    return driver


class GraphSearch:
    """S1 ~ S3 구성요소를 한 번만 만들어 여러 질문에 재사용함."""

    def __init__(self, driver) -> None:
        from neo4j_graphrag.embeddings import CohereEmbeddings
        from neo4j_graphrag.retrievers import VectorCypherRetriever, VectorRetriever
        from typesafe_sdk import RetryPolicy, TypeSafeClient

        self.driver = driver
        # 색인과 같은 Cohere 모델. 질문 쪽이므로 input_type=search_query
        embedder = CohereEmbeddings(model=index_graph.EMBEDDING_MODEL, input_type="search_query",
                                    api_key=os.environ["COHERE_API_KEY"])
        # Jev 키는 SDK 기본 변수(TYPESAFE_API_KEY)가 아닌 JEV_API_KEY를 직접 넘김. 1회 호출을 지키려 재시도는 끔
        self.router = TypeSafeClient(api_key=os.environ["JEV_API_KEY"], model=ROUTER_MODEL,
                                     retry=RetryPolicy(max_retries=0))
        self.local_retriever = VectorCypherRetriever(
            driver, index_name=index_graph.ENTITY_INDEX, retrieval_query=LOCAL_RETRIEVAL_QUERY,
            embedder=embedder, result_formatter=format_local_record)
        self.global_retriever = VectorRetriever(
            driver, index_name=index_graph.COMMUNITY_INDEX, embedder=embedder,
            return_properties=["id", "size", "summary"], result_formatter=format_global_record)
        self.llm = schema_common.make_llm(reasoning_effort="medium", max_tokens=8000, json_mode=False)

    def close(self) -> None:
        self.router.close()

    # -----------------------------------------------------------------------
    # S1. 라우팅: Jev 1회 호출로 local / global / hybrid 중 하나를 고름
    # -----------------------------------------------------------------------
    def route(self, question: str) -> Route:
        from typesafe_sdk import Choice

        try:
            response = self.router.system_one(
                state=question,
                questions={"route": Choice(instructions=ROUTE_INSTRUCTIONS, criteria=ROUTE_CRITERIA)},
            )
            answer = response.choices["route"]
            if answer.choice not in ROUTES:
                raise ValueError(f"알 수 없는 선택지: {answer.choice}")
            return Route(choice=answer.choice, confidence=answer.confidence,
                         probabilities=dict(answer.probabilities))
        except Exception as e:  # 호출 실패 → hybrid
            return Route(choice=FALLBACK_ROUTE, confidence=None, fallback=True, error=type(e).__name__)

    # -----------------------------------------------------------------------
    # S2. 검색: Local(엔티티+이웃 관계+출처 청크), Global(커뮤니티 요약), Hybrid(둘 다)
    # -----------------------------------------------------------------------
    def search_local(self, question: str) -> list[dict[str, Any]]:
        result = self.local_retriever.search(
            query_text=question, top_k=LOCAL_TOP_K, query_params={"reserved": index_graph.RESERVED_LABELS})
        return [item.metadata for item in result.items]

    def search_global(self, question: str) -> list[dict[str, Any]]:
        result = self.global_retriever.search(query_text=question, top_k=GLOBAL_TOP_K)
        return [item.metadata for item in result.items]

    def retrieve(self, question: str, route: str) -> tuple[list[str], list[dict], list[dict]]:
        executed, local, global_ = [], [], []
        if route in ("local", "hybrid"):
            local = self.search_local(question)
            executed.append("local")
        if route in ("global", "hybrid"):
            global_ = self.search_global(question)
            executed.append("global")
        return executed, local, global_

    # -----------------------------------------------------------------------
    # S3. 답변 생성: 컨텍스트와 질문으로 Groq 1회 호출, 한국어 답 + 근거
    # -----------------------------------------------------------------------
    def generate(self, question: str, context: str) -> str:
        res = self.llm.invoke(ANSWER_PROMPT.format(question=question, context=context),
                              system_instruction=ANSWER_SYSTEM)
        return (res.content or "").strip()

    def ask(self, question: str, forced_route: str | None = None) -> SearchResult:
        route = Route(choice=forced_route, confidence=None) if forced_route else self.route(question)   # S1
        executed, local, global_ = self.retrieve(question, route.choice)                                 # S2
        context = build_context(local, global_)
        if not local and not global_:   # 근거 0건이면 S3으로 넘기지 않음
            return SearchResult(question, route, executed, local, global_, "", NO_EVIDENCE)
        answer = self.generate(question, context)                                                        # S3
        return SearchResult(question, route, executed, local, global_, context, answer)


# ---------------------------------------------------------------------------
# S2 결과 정리
# ---------------------------------------------------------------------------
def format_local_record(record):
    from neo4j_graphrag.types import RetrieverResultItem

    data = {"type": record["type"], "name": record["name"], "score": record["score"],
            "relations": record["relations"], "chunks": record["chunks"]}
    return RetrieverResultItem(content=f"{data['type']}: {data['name']}", metadata=data)


def format_global_record(record):
    from neo4j_graphrag.types import RetrieverResultItem

    node = record["node"]
    data = {"id": node.get("id"), "size": node.get("size"), "summary": node.get("summary"), "score": record["score"]}
    return RetrieverResultItem(content=data["summary"] or "", metadata=data)


def build_context(local: list[dict], global_: list[dict]) -> str:
    """S3에 넘길 컨텍스트. 같은 청크가 여러 엔티티에 걸리면 한 번만 넣음."""
    lines: list[str] = []
    if local:
        lines.append("## 엔티티와 관계(Local)")
        for e in local:
            lines.append(f"- {e['type']}: {e['name']} (유사도 {e['score']:.3f})")
            lines += [f"  - {r}" for r in e["relations"]]
        chunks: dict[int, str] = {}
        for e in local:
            for c in e["chunks"]:
                chunks.setdefault(c["chunk_index"], c["text"])
        if chunks:
            lines.append("\n## 출처 청크 본문(Local)")
            lines += [f"[chunk {i}]\n{text}\n" for i, text in chunks.items()]
    if global_:
        lines.append("## 커뮤니티 요약(Global)")
        lines += [f"- [커뮤니티 {c['id']}, 엔티티 {c['size']}개, 유사도 {c['score']:.3f}] {c['summary']}" for c in global_]
    return "\n".join(lines)


ANSWER_SYSTEM = "당신은 특허법 지식그래프 검색 결과만 근거로 답하는 조수입니다."
ANSWER_PROMPT = """[질문]
{question}

[컨텍스트]
{context}

[답변 규칙]
- 한국어로 답함
- 컨텍스트에 있는 내용만 씀. 컨텍스트로 답할 수 없는 부분은 "컨텍스트에서 확인되지 않음"이라고 씀
- 답 끝에 "근거:" 줄을 두고, 답에 쓴 조문 번호(예: 제29조) 또는 엔티티 이름을 쉼표로 나열함
"""


# ---------------------------------------------------------------------------
# 화면 출력
# ---------------------------------------------------------------------------
def route_text(route: Route) -> str:
    if route.fallback:
        return f"{route.choice} (Jev 호출 실패 → 기본값, {route.error})"
    if route.confidence is None:
        return f"{route.choice} (--route로 지정)"
    return f"{route.choice} (확신도 {route.confidence:.2f})"


def print_result(result: SearchResult, verbose: bool) -> None:
    print(f"[질문] {result.question}")
    print(f"[검색 방식] {route_text(result.route)}")
    if verbose:
        print(f"[실행한 검색] {', '.join(result.executed)}")
        print("[검색된 컨텍스트]")
        print(result.context or "(없음)")
    print("[답변]")
    print(result.answer)


def main() -> None:
    parser = argparse.ArgumentParser(description="특허법 GraphRAG 검색(S1 라우팅 → S2 검색 → S3 답변)")
    parser.add_argument("question", help="질문")
    parser.add_argument("--verbose", action="store_true", help="검색된 컨텍스트도 보여 줌")
    parser.add_argument("--route", choices=ROUTES, help="S1을 건너뛰고 검색 방식을 직접 지정(실습 비교용)")
    args = parser.parse_args()
    schema_common.setup_console()

    driver = precheck()
    search = GraphSearch(driver)
    try:
        print_result(search.ask(args.question, args.route), args.verbose)
    finally:
        search.close()
        driver.close()


if __name__ == "__main__":
    main()
