"""ReAct 검색 에이전트: LLM이 생각 → 도구 호출 → 관찰을 반복하며 스스로 다음 행동을 고름.

고정 워크플로우(search_eval.py의 AgenticSearch: 분해 → 검색 → 답변 → 검증을 매번 같은 순서로)와 달리,
어떤 도구를 몇 번 쓸지, 언제 멈출지를 LLM이 정함. LangChain 1.x `create_agent`(ReAct 루프)를 씀.

도구 5개(모두 읽기 전용 Cypher, 읽기 트랜잭션으로 실행해 그래프에 쓸 수 없음)
- search_entities: 질문·개념과 가까운 엔티티 상위 5개와 각자의 관계(벡터 인덱스 entity_embedding)
- get_neighbors: 엔티티 이름으로 1단계 이웃 관계와 출처 청크 번호를 펼침
- find_relations: 스키마 관계 타입과 이름 일부로 패턴 조회(예: HANDLED_BY, 시작 이름에 '심판')
- search_communities: 법 전체 주제·흐름을 담은 커뮤니티 요약 상위 3개(벡터 인덱스 community_embedding)
- read_chunk: 출처 청크 번호로 조문 원문을 읽음

안전장치: 모델 호출 최대 8회, 도구 호출 최대 10회. 상한에 걸려 최종 답이 없으면 모은 관찰로 답변 1회를 따로 만듦.

실행(저장소 루트에서):
    python graphrag/experiments/agentic/react_search.py "특허거절결정에 불복하려면 누구에게 어떤 심판을 청구하는가?"
"""

from __future__ import annotations

import os
import sys
import time

from harness import EXP_DIR
from common import ENV_FILE, MODEL_NAME, setup_console

sys.path.insert(0, str(EXP_DIR.parents[1] / "search"))
import graph_search  # noqa: E402  (원래 검색 프로그램: 접속 설정·검색기 재사용, 읽기만 함)
from dotenv import load_dotenv  # noqa: E402

MAX_MODEL_CALLS = 8
MAX_TOOL_CALLS = 10
RESERVED = ["__Entity__", "__KGBuilder__"]
REL_TYPES = ["ELIGIBLE_FOR", "NOT_ELIGIBLE_FOR", "CAN_FILE", "HANDLED_BY", "TARGETS", "HAS_DEADLINE",
             "HAS_DURATION", "NOT_EFFECTIVE_FOR", "HOLDS", "CAN_CLAIM", "CLAIMED_AGAINST", "PUNISHED_BY"]

SYSTEM_PROMPT = f"""당신은 특허법 지식그래프를 도구로 탐색해 질문에 답하는 조수입니다.

[그래프 구성]
- 노드: Actor(주체·기관), Right(권리), Invention(발명 유형), Procedure(절차), Decision(결정), Period(기간), Penalty(형벌), Exclusion(효력 제외 사유), Remedy(구제 수단), Chunk(조문 원문 조각)
- 관계: {', '.join(REL_TYPES)}. 예: (Actor)-[CAN_FILE]->(Procedure), (Procedure)-[HANDLED_BY]->(Actor), (Procedure)-[HAS_DEADLINE]->(Period), (Actor)-[CAN_CLAIM]->(Remedy)
- 출처 청크 번호(#103 등)는 조문 원문 조각의 일련번호이며 조문 번호(제N조)가 아님. 조문 번호는 read_chunk로 읽은 원문에서 확인함

[일하는 방식]
1. 질문을 이루는 하위 질문(누가, 무엇을, 누구에게, 언제까지, 어떤 효과)을 먼저 생각함
2. 도구로 근거를 모음. 찾은 엔티티의 관계가 부족하면 get_neighbors나 find_relations로 더 따라감. 법 전체 흐름을 묻는 질문은 search_communities도 씀
3. 기한·형량·기관 이름처럼 정확해야 하는 내용은 read_chunk로 조문 원문을 확인함
4. 하위 질문마다 근거가 모이면 멈추고 답함. 같은 도구를 같은 입력으로 반복하지 않음

[답변 규칙]
- 한국어로 답함
- 도구 결과에 있는 내용만 씀. 확인되지 않은 부분은 "그래프에서 확인되지 않음"이라고 씀
- 답 끝에 "근거:" 줄을 두고, 답에 쓴 조문 번호(예: 제29조, 원문에서 확인한 것만) 또는 엔티티 이름을 쉼표로 나열함. 청크 번호는 쓰지 않음
"""

# v2: 도구 결과에 출처 조문 원문을 함께 넣음(1차 비교에서 원문을 읽은 답 0.905, 안 읽은 답 0.516이었기 때문)
V2_RULES = """
[v2 추가 규칙]
- 도구 결과 끝의 "출처 원문"은 그 엔티티·관계가 뽑힌 조문 원문임. 주체·조건·기한·처리 기관·형량은 원문으로 확인해 답에 담음
- 관계만으로 답하지 말고, 질문의 하위 질문마다 원문 근거가 있는지 확인한 뒤 답함. 원문이 안 보이면 read_chunk를 씀
"""

FINAL_PROMPT = """아래는 특허법 지식그래프 도구로 모은 관찰입니다. 이것만으로 질문에 답하세요.

[질문]
{question}

[관찰]
{observations}

[답변 규칙]
- 한국어로 답함. 관찰에 있는 내용만 씀. 확인되지 않은 부분은 "그래프에서 확인되지 않음"
- 답 끝에 "근거:" 줄을 두고 조문 번호 또는 엔티티 이름을 쉼표로 나열함
"""


class ReactSearch:
    """원래 그래프(또는 지정한 그래프)를 읽기 전용 도구로 탐색하는 ReAct 에이전트."""

    def __init__(self, driver, with_source: bool = False, retries: int = 3) -> None:
        """with_source=True면 v2(도구 결과에 출처 원문 포함). retries는 Groq 형식 오류 때 처음부터 다시 시도하는 횟수(평가에서는 바깥에서 같은 정책을 쓰려고 1)."""
        import neo4j
        from langchain.agents import create_agent
        from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
        from langchain_groq import ChatGroq

        self.driver = driver
        self.with_source = with_source
        self.retries = retries
        self._shown: set[int] = set()
        self._read = neo4j.RoutingControl.READ
        self.base = graph_search.GraphSearch(driver)   # 원래 검색기(Local/Global 벡터 검색) 재사용
        self.llm = ChatGroq(model=MODEL_NAME, temperature=0, reasoning_effort="medium",
                            api_key=os.environ["GROQ_API_KEY"], max_retries=3)
        self.agent = create_agent(
            self.llm, self._tools(), system_prompt=SYSTEM_PROMPT + (V2_RULES if with_source else ""),
            middleware=[ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="end"),
                        ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS)])

    def close(self) -> None:
        self.base.close()

    def _q(self, query: str, **params) -> list[dict]:
        """읽기 트랜잭션으로 실행(쓰기 쿼리는 Neo4j가 거부함)."""
        return [r.data() for r in self.driver.execute_query(query, routing_=self._read, **params).records]

    def _sources(self, idxs: list[int], limit: int = 3) -> str:
        """v2: 출처 청크 원문을 붙임. 같은 질문 안에서 이미 보여 준 원문은 다시 넣지 않음."""
        if not self.with_source:
            return ""
        idxs = [int(i) for i in dict.fromkeys(idxs)]
        new = [i for i in idxs if i not in self._shown][:limit]
        old = [i for i in idxs if i in self._shown]
        parts = []
        if new:
            rows = self._q("MATCH (c:Chunk) WHERE c.index IN $ids RETURN c.index AS i, c.text AS t", ids=new)
            text = {r["i"]: r["t"] for r in rows}
            for i in new:
                if i in text:
                    self._shown.add(i)
                    parts.append(f"[청크 #{i}]\n{text[i]}")
        if old:
            parts.append(f"(청크 #{', #'.join(map(str, old[:5]))} 원문은 앞의 관찰에 있음)")
        return ("\n\n## 출처 원문\n" + "\n\n".join(parts)) if parts else ""

    # -----------------------------------------------------------------------
    # 도구
    # -----------------------------------------------------------------------
    def _tools(self):
        from langchain.tools import tool

        @tool
        def search_entities(query: str) -> str:
            """질문이나 개념(예: '특허권 침해 구제', '출원심사 청구 기한')과 의미가 가까운 엔티티 상위 5개와 각 엔티티의 관계를 찾는다."""
            items = self.base.search_local(query)
            if not items:
                return "결과 없음"
            lines = []
            for e in items:
                chunks = ", #".join(str(c["chunk_index"]) for c in e["chunks"])
                lines.append(f"- {e['type']}: {e['name']} (유사도 {e['score']:.2f}, 출처 청크 #{chunks or '-'})")
                lines += [f"  · {r}" for r in e["relations"][:12]]
            return "\n".join(lines) + self._sources([c["chunk_index"] for e in items for c in e["chunks"]])

        @tool
        def get_neighbors(entity_name: str) -> str:
            """엔티티 이름(전체 또는 일부)으로 그 엔티티의 1단계 이웃 관계와 출처 청크 번호를 펼친다."""
            rows = self._q(
                "MATCH (e:__Entity__) WHERE e.name CONTAINS $n "
                "WITH e ORDER BY size(e.name) LIMIT 3 "
                "RETURN [l IN labels(e) WHERE NOT l IN $reserved][0] AS type, e.name AS name, "
                "collect { MATCH (e)-[r]-(o:__Entity__) RETURN CASE WHEN startNode(r) = e "
                "  THEN e.name + ' -' + type(r) + '-> ' + o.name ELSE o.name + ' -' + type(r) + '-> ' + e.name END LIMIT 25 } AS rels, "
                # 출처 청크는 이웃 엔티티가 함께 나온 청크(관계가 뽑힌 청크)를 먼저 보여 줌(원래 Local 검색과 같은 기준)
                "collect { MATCH (e)-[:FROM_CHUNK]->(c:Chunk) "
                "  WITH c, COUNT { (c)<-[:FROM_CHUNK]-(:__Entity__)--(e) } AS overlap "
                "  RETURN c.index ORDER BY overlap DESC, c.index LIMIT 5 } AS chunks",
                n=entity_name, reserved=RESERVED)
            if not rows:
                return f"'{entity_name}'을(를) 이름에 포함한 엔티티 없음. search_entities로 찾아볼 것"
            out = []
            for r in rows:
                out.append(f"- {r['type']}: {r['name']} (출처 청크 #{', #'.join(map(str, r['chunks'])) or '-'})")
                out += [f"  · {x}" for x in r["rels"]] or ["  · (관계 없음)"]
            return "\n".join(out) + self._sources([i for r in rows for i in r["chunks"][:2]])

        @tool
        def find_relations(relation_type: str, start_contains: str = "", end_contains: str = "") -> str:
            """스키마 관계 타입으로 관계를 조회한다. relation_type은 ELIGIBLE_FOR, NOT_ELIGIBLE_FOR, CAN_FILE, HANDLED_BY, TARGETS,
            HAS_DEADLINE, HAS_DURATION, NOT_EFFECTIVE_FOR, HOLDS, CAN_CLAIM, CLAIMED_AGAINST, PUNISHED_BY 중 하나.
            start_contains·end_contains로 시작·끝 노드 이름 일부를 걸러낸다(빈 문자열이면 거르지 않음)."""
            if relation_type not in REL_TYPES:
                return f"알 수 없는 관계 타입: {relation_type}. 가능한 값: {', '.join(REL_TYPES)}"
            rows = self._q(
                f"MATCH (a:__Entity__)-[r:{relation_type}]->(b:__Entity__) "
                "WHERE a.name CONTAINS $s AND b.name CONTAINS $e "
                "OPTIONAL MATCH (a)-[:FROM_CHUNK]->(c:Chunk) "
                # 관계가 뽑힌 청크(양 끝이 함께 나온 청크)를 먼저 둠
                "WITH a, b, c ORDER BY CASE WHEN EXISTS { (b)-[:FROM_CHUNK]->(c) } THEN 0 ELSE 1 END, c.index "
                "RETURN a.name AS a, b.name AS b, collect(DISTINCT c.index)[..3] AS chunks LIMIT 20",
                s=start_contains, e=end_contains)
            if not rows:
                return "결과 없음"
            body = "\n".join(f"- {r['a']} -{relation_type}-> {r['b']} (출처 청크 #{', #'.join(map(str, r['chunks'])) or '-'})" for r in rows)
            return body + self._sources([r["chunks"][0] for r in rows if r["chunks"]])

        @tool
        def search_communities(query: str) -> str:
            """법 전체의 주제나 흐름을 묻는 질문에 쓴다. 질문과 가까운 커뮤니티(서로 촘촘히 이어진 엔티티 묶음) 요약 상위 3개를 찾는다."""
            items = self.base.search_global(query)
            return "\n".join(f"- [커뮤니티 {c['id']}, 엔티티 {c['size']}개] {c['summary']}" for c in items) or "결과 없음"

        @tool
        def read_chunk(chunk_index: int) -> str:
            """출처 청크 번호로 조문 원문을 읽는다. 기한·형량·기관 이름을 원문으로 확인할 때 쓴다."""
            rows = self._q("MATCH (c:Chunk) WHERE c.index = $i RETURN c.text AS text LIMIT 1", i=int(chunk_index))
            return rows[0]["text"] if rows else f"청크 {chunk_index} 없음"

        tools = [search_entities, get_neighbors, find_relations, search_communities, read_chunk]
        return [self._no_repeat(t) for t in tools]

    def _no_repeat(self, t):
        """같은 도구를 같은 입력으로 다시 부르면 실행하지 않고 알려 줌(반복 루프 방지). 질문마다 기록을 비움(ask)."""
        original = t.func

        def wrapped(*args, **kwargs):
            key = (t.name, repr(args), repr(sorted(kwargs.items())))
            if key in self._seen:
                return "이미 같은 입력으로 호출한 결과가 위에 있음. 다른 도구나 다른 입력을 쓰거나, 근거가 충분하면 답할 것"
            self._seen.add(key)
            return original(*args, **kwargs)

        t.func = wrapped
        return t

    # -----------------------------------------------------------------------
    # 실행
    # -----------------------------------------------------------------------
    def ask(self, question: str) -> dict:
        """ReAct 루프를 돌려 답과 실행 기록(도구 호출 순서, 관찰, 모델·도구 호출 수, 소요 시간)을 돌려줌."""
        from langchain_core.messages import AIMessage, ToolMessage

        started = time.time()
        errors: list[str] = []
        for _ in range(self.retries):   # Groq가 도구 호출 형식을 깨뜨리는 오류(400 output_parse_failed 등)는 처음부터 다시 시도
            self._seen: set = set()
            self._shown = set()
            try:
                result = self.agent.invoke({"messages": [{"role": "user", "content": question}]})
                break
            except Exception as e:
                errors.append(type(e).__name__)
        else:
            return {"answer": graph_search.NO_EVIDENCE, "context": "", "steps": [], "model_calls": 0, "tool_calls": 0,
                    "forced_final": False, "errors": errors, "failed": True, "seconds": round(time.time() - started, 1)}
        msgs = result["messages"]
        ai = [m for m in msgs if isinstance(m, AIMessage) and "call limits exceeded" not in str(m.content).lower()]
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        steps = [{"tool": tc["name"], "args": tc["args"]} for m in ai for tc in (m.tool_calls or [])]
        observations = "\n\n".join(f"[{m.name}]\n{m.content}" for m in tool_msgs)
        last = msgs[-1]
        answer = last.content.strip() if isinstance(last, AIMessage) and not last.tool_calls and last.content else ""
        if "call limits exceeded" in answer.lower():   # 상한 미들웨어가 남긴 안내 문구는 답이 아님
            answer = ""
        forced = False
        if not answer:   # 상한에 걸려 최종 답이 없으면 모은 관찰로 답변 1회
            forced = True
            answer = (self.llm.invoke(FINAL_PROMPT.format(question=question, observations=observations or "(없음)")).content or "").strip()
        return {"answer": answer or graph_search.NO_EVIDENCE, "context": observations, "steps": steps,
                "model_calls": len(ai) + (1 if forced else 0), "tool_calls": len(tool_msgs), "forced_final": forced,
                "errors": errors, "failed": False, "seconds": round(time.time() - started, 1)}


def main() -> None:
    setup_console()
    load_dotenv(ENV_FILE)
    driver = graph_search.precheck()
    agent = ReactSearch(driver, with_source="--v2" in sys.argv)   # --v2: 도구 결과에 출처 원문 포함
    try:
        r = agent.ask([a for a in sys.argv[1:] if a != "--v2"][0])
        for i, s in enumerate(r["steps"], 1):
            print(f"[행동 {i}] {s['tool']}({s['args']})")
        print(f"[모델 {r['model_calls']}회, 도구 {r['tool_calls']}회, {r['seconds']}초{', 상한 도달로 답 따로 생성' if r['forced_final'] else ''}]")
        print(r["answer"])
    finally:
        agent.close()
        driver.close()


if __name__ == "__main__":
    main()
