"""검색 답변 품질 비교: 그래프(원래 7687 / 실험 7688) × 검색(원래 / 에이전틱).

- 원래 검색: graphrag/search/graph_search.py의 GraphSearch 그대로(읽기만 함). S1 Jev → S2 → S3 Groq 1회
- 에이전틱 검색(최아이 R1·R2): S1 Jev → 질의 분해(Groq 1회, 핵심 개념 1 ~ 3개) → 개념별 Local 검색을 합침
  → 답변(Groq 1회) → 근거 검증·수정(Groq 1회, 컨텍스트로 뒷받침되지 않는 기한·형량·조문 번호를 지움)
- 채점(윤큐 제안): 답변 모델과 다른 계열의 채점 모델(qwen/qwen3.8-27b)
  - 요점 재현율(역량질문 9개): 확정된 요점 체크리스트(cq-keypoints.json)의 요점마다 답에 있는지 예/아니오, 3회 다수결
  - 근거 충실도(13개 전부): 답의 원자 주장마다 검색 컨텍스트로 뒷받침되는지 판정한 비율
  - 채점 모델에는 어느 그래프·검색 방식의 답인지 알려 주지 않음
그래프는 읽기만 함(두 그래프 모두 쓰기·삭제 없음).

실행(저장소 루트에서):
    python graphrag/experiments/agentic/search_eval.py --repeat 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter

from harness import EXP_DIR, RESULTS_DIR
from common import ENV_FILE, setup_console, write_json

sys.path.insert(0, str(EXP_DIR.parents[1] / "search"))
import graph_search  # noqa: E402  (원래 검색 프로그램, 읽기만 함)
import run_tests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

GRAPHS = {"orig": "bolt://localhost:7687", "agentic": "bolt://localhost:7688"}
JUDGE_MODEL = "qwen/qwen3.8-27b"
KEY_FILE = EXP_DIR / "cq-keypoints.json"
OUT_FILE = RESULTS_DIR / "search-eval.json"
REPORT = EXP_DIR / "search-eval-report.md"
MAX_LOCAL = 10


# ---------------------------------------------------------------------------
# 에이전틱 검색
# ---------------------------------------------------------------------------
DECOMPOSE_PROMPT = """[목표] 특허법 지식그래프에서 엔티티를 찾기 위해, 질문을 검색어 1 ~ 3개로 나눔
[질문] {question}
[처리] 질문에 든 핵심 개념(주체, 권리, 절차, 결정, 기간, 구제, 형벌 등)마다 짧은 검색어 하나. 질문 자체도 첫 검색어로 넣음
[출력] JSON 하나만: {{"queries": ["질문", "검색어", ...]}}"""

VERIFY_PROMPT = """[목표] 아래 답변을 컨텍스트와 대조해, 컨텍스트로 뒷받침되지 않는 내용을 지운 최종 답변을 만듦

[질문]
{question}

[컨텍스트]
{context}

[답변]
{answer}

[처리]
- 문장마다 컨텍스트에 근거가 있는지 확인함. 특히 기한·기간·형량·금액·조문 번호·기관 이름은 컨텍스트와 글자 그대로 맞는지 확인함
- 근거가 없는 문장은 지우거나 컨텍스트에 맞게 고침. 컨텍스트에 없는 해석·평가 표현은 지움
- 근거가 있는 내용은 지우지 않음. 마지막 "근거:" 줄은 남기되 컨텍스트에 있는 조문 번호·엔티티 이름만 둠
[출력] 최종 답변 본문만(설명 없이)"""


class AgenticSearch(graph_search.GraphSearch):
    async def decompose(self, question: str) -> list[str]:
        try:
            res = await self.llm.ainvoke(DECOMPOSE_PROMPT.format(question=question))
            text = res.content or ""
            qs = json.loads(text[text.find("{"): text.rfind("}") + 1]).get("queries", [])
            qs = [q for q in qs if isinstance(q, str) and q.strip()][:3]
        except Exception:
            qs = []
        return qs or [question]

    def search_local_multi(self, queries: list[str]) -> list[dict]:
        best: dict[tuple, dict] = {}
        for q in queries:
            for e in self.search_local(q):
                key = (e["type"], e["name"])
                if key not in best or e["score"] > best[key]["score"]:
                    best[key] = e
        return sorted(best.values(), key=lambda e: -e["score"])[:MAX_LOCAL]

    async def ask_agentic(self, question: str) -> dict:
        route = self.route(question)
        executed, local, global_ = [], [], []
        queries = [question]
        if route.choice in ("local", "hybrid"):
            queries = await self.decompose(question)
            local = self.search_local_multi(queries)
            executed.append("local")
        if route.choice in ("global", "hybrid"):
            global_ = self.search_global(question)
            executed.append("global")
        context = graph_search.build_context(local, global_)
        if not local and not global_:
            return {"route": route, "context": "", "answer": graph_search.NO_EVIDENCE, "draft": "", "queries": queries}
        draft = self.generate(question, context)
        res = await self.llm.ainvoke(VERIFY_PROMPT.format(question=question, context=context, answer=draft))
        answer = (res.content or "").strip() or draft
        return {"route": route, "context": context, "answer": answer, "draft": draft, "queries": queries}


# ---------------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------------
KEYPOINT_PROMPT = """[목표] 답변에 정답 요점이 들어 있는지 요점마다 판정함
[질문] {question}
[답변]
{answer}
[요점]
{points}
[판정] 요점의 사실(주체·절차·기한·기관·효과·형량)이 답변에 같은 뜻으로 들어 있으면 true. 일부만 있거나 다르면 false
[출력] JSON 하나만: {{"hits": [true, false, ...]}} (요점 수와 같은 길이)"""

FAITH_PROMPT = """[목표] 답변의 주장이 검색 컨텍스트로 뒷받침되는지 판정함
[컨텍스트]
{context}
[답변]
{answer}
[처리] 답변을 사실 주장 단위로 나눔("근거:" 줄은 제외). 주장마다 컨텍스트에 같은 내용이 있으면 supported=true
[출력] JSON 하나만: {{"claims": [{{"claim": "주장", "supported": true}}]}}"""


def make_judge():
    from neo4j_graphrag.llm import OpenAILLM
    from neo4j_graphrag.utils.rate_limit import RetryRateLimitHandler

    return OpenAILLM(model_name=JUDGE_MODEL, model_params={"temperature": 0, "response_format": {"type": "json_object"}},
                     base_url="https://api.groq.com/openai/v1", api_key=os.environ["GROQ_API_KEY"],
                     rate_limit_handler=RetryRateLimitHandler(max_attempts=5, max_wait=60))


async def judge_json(judge, prompt: str) -> dict:
    for _ in range(3):
        try:
            res = await judge.ainvoke(prompt)
            text = res.content or ""
            return json.loads(text[text.find("{"): text.rfind("}") + 1])
        except Exception:
            continue
    return {}


async def keypoint_recall(judge, item: dict, answer: str, votes: int = 3) -> float:
    points = "\n".join(f"{i + 1}. {k['point']}" for i, k in enumerate(item["keypoints"]))
    prompt = KEYPOINT_PROMPT.format(question=item["question"], answer=answer, points=points)
    results = await asyncio.gather(*(judge_json(judge, prompt) for _ in range(votes)))
    n = len(item["keypoints"])
    hits = []
    for i in range(n):
        yes = sum(1 for r in results if isinstance(r.get("hits"), list) and len(r["hits"]) == n and r["hits"][i] is True)
        hits.append(yes * 2 > votes)
    return sum(hits) / n


async def faithfulness(judge, context: str, answer: str) -> float | None:
    if not context:
        return None
    r = await judge_json(judge, FAITH_PROMPT.format(context=context, answer=answer))
    claims = [c for c in r.get("claims", []) if isinstance(c, dict)]
    return sum(1 for c in claims if c.get("supported") is True) / len(claims) if claims else None


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def driver_for(uri: str):
    import neo4j

    _, password = graph_search.index_graph.neo4j_settings()
    d = neo4j.GraphDatabase.driver(uri, auth=(graph_search.index_graph.NEO4J_USER, password), notifications_min_severity="OFF")
    d.verify_connectivity()
    return d


async def answer_one(search: AgenticSearch, mode: str, question: str) -> dict:
    if mode == "agentic":
        r = await search.ask_agentic(question)
        return {"route": r["route"].choice, "confidence": r["route"].confidence, "context": r["context"],
                "answer": r["answer"], "draft": r["draft"], "queries": r["queries"]}
    r = search.ask(question)
    return {"route": r.route.choice, "confidence": r.route.confidence, "context": r.context, "answer": r.answer}


async def main() -> None:
    setup_console()
    load_dotenv(ENV_FILE)
    p = argparse.ArgumentParser()
    p.add_argument("--repeat", type=int, default=2)
    p.add_argument("--graphs", nargs="+", choices=list(GRAPHS), default=list(GRAPHS), help="비교할 그래프(기본: 둘 다)")
    p.add_argument("--tag", default="", help="결과 파일 이름에 붙일 표시(추가 반복 등)")
    args = p.parse_args()
    out_file = OUT_FILE.with_name(f"search-eval{'-' + args.tag if args.tag else ''}.json")
    report = REPORT.with_name(f"search-eval-report{'-' + args.tag if args.tag else ''}.md")
    keys = json.loads(KEY_FILE.read_text(encoding="utf-8"))
    if keys.get("status") != "확정":
        sys.exit("[중단] cq-keypoints.json이 확정되지 않음")
    key_by_q = {k["question"]: k for k in keys["items"]}
    questions = run_tests.load_questions()
    judge = make_judge()
    rows = []
    for gname, uri in ((g, GRAPHS[g]) for g in args.graphs):
        driver = driver_for(uri)
        search = AgenticSearch(driver)
        try:
            for rep in range(args.repeat):
                for mode in ("base", "agentic"):
                    for q in questions:
                        a = await answer_one(search, mode, q["question"])
                        item = key_by_q.get(q["question"])
                        kp = await keypoint_recall(judge, item, a["answer"]) if item else None
                        fa = await faithfulness(judge, a["context"], a["answer"])
                        rows.append({"graph": gname, "search": mode, "repeat": rep, **q, **a,
                                     "keypoint_recall": kp, "faithfulness": fa})
                        print(f"  [{gname}/{mode}/{rep + 1}] {q['id']} 경로 {a['route']} 요점 {kp if kp is None else round(kp, 2)} "
                              f"충실도 {fa if fa is None else round(fa, 2)}", flush=True)
        finally:
            search.close()
            driver.close()
    write_json(out_file, rows)
    report.write_text(render(rows, args.repeat), encoding="utf-8")
    print(f"[보고서] {report.name}")


def mean(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def fmt(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f}"


def render(rows: list[dict], repeat: int) -> str:
    combos = [(g, s) for g in dict.fromkeys(r["graph"] for r in rows) for s in ("base", "agentic")]
    names = {("orig", "base"): "원래 그래프 + 원래 검색", ("orig", "agentic"): "원래 그래프 + 에이전틱 검색",
             ("agentic", "base"): "실험 그래프 + 원래 검색", ("agentic", "agentic"): "실험 그래프 + 에이전틱 검색"}
    lines = ["# 검색 답변 품질 비교", "",
             f"- 시험 질문: graphrag/search/test-questions.md(확정본 13개), 조합마다 {repeat}회",
             f"- 채점 모델: {JUDGE_MODEL}(답변 모델 openai/gpt-oss-120b와 다른 계열), 어느 조합의 답인지 알려 주지 않음",
             "- 요점 재현율: 역량질문 9개, 확정된 요점(cq-keypoints.json 31개) 중 답에 든 비율(3회 다수결)",
             "- 근거 충실도: 13개 전부, 답의 주장 중 검색 컨텍스트로 뒷받침되는 비율", "",
             "## 1. 요약", "", "| 조합 | 요점 재현율(역량질문) | 근거 충실도(전체) | 근거 충실도(Global·Hybrid) |", "|---|---|---|---|"]
    for c in combos:
        rs = [r for r in rows if (r["graph"], r["search"]) == c]
        gh = [r for r in rs if r["expected"] in ("global", "hybrid")]
        lines.append(f"| {names[c]} | {fmt(mean([r['keypoint_recall'] for r in rs]))} | "
                     f"{fmt(mean([r['faithfulness'] for r in rs]))} | {fmt(mean([r['faithfulness'] for r in gh]))} |")
    lines += ["", "## 2. 질문별 요점 재현율 / 근거 충실도(반복 평균)", "",
              "| ID | " + " | ".join(names[c] for c in combos) + " |", "|---|" + "---|" * len(combos)]
    for qid in dict.fromkeys(r["id"] for r in rows):
        cells = []
        for c in combos:
            rs = [r for r in rows if (r["graph"], r["search"]) == c and r["id"] == qid]
            cells.append(f"{fmt(mean([r['keypoint_recall'] for r in rs]))} / {fmt(mean([r['faithfulness'] for r in rs]))}")
        lines.append(f"| {qid} | " + " | ".join(cells) + " |")
    routes = Counter((r["graph"], r["search"], r["route"]) for r in rows)
    lines += ["", "## 3. 선택된 검색 방식(전 반복 합계)", ""]
    lines += [f"- {names[c]}: " + ", ".join(f"{k} {routes[(c[0], c[1], k)]}" for k in ("local", "global", "hybrid")) for c in combos]
    lines += ["", "## 4. 답변 예시(1회차)", ""]
    for qid in dict.fromkeys(r["id"] for r in rows):
        for c in combos:
            r = next(x for x in rows if (x["graph"], x["search"]) == c and x["id"] == qid and x["repeat"] == 0)
            lines += [f"### {qid} · {names[c]}", "", r["answer"].strip(), ""]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    asyncio.run(main())
