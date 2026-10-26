"""시험 질문 초안 만들기 → test-questions.md(상태: 검토 중).

역량질문 9개(기대 검색 방식 local)에 Global형 2개, Hybrid형 2개를 LLM(Groq) 1회 호출로 더 씀.
LLM이 그래프 범위 안에서 질문을 쓰도록 커뮤니티 요약(읽기 전용 조회)을 함께 넘김.
사람이 검토해 `상태: 확정`으로 바꾼 뒤에만 run_tests.py가 시험을 돌림.
"""

from __future__ import annotations

import asyncio
from datetime import date

import graph_search
from graph_search import SEARCH_DIR, schema_common

TEST_FILE = SEARCH_DIR / "test-questions.md"
N_GLOBAL, N_HYBRID = 2, 2
MAX_SUMMARIES = 15

PROMPT = """아래는 특허법 지식그래프의 역량질문과 커뮤니티 요약(서로 촘촘히 이어진 엔티티 묶음의 요약)이다.
GraphRAG 검색 실습용 시험 질문을 더 만들어라.

[검색 방식]
- global: {global_desc}
- hybrid: {hybrid_desc}

[만들 질문]
- global형 {n_global}개: 특정 조문 하나가 아니라 법 전체의 주제나 흐름을 묻는 질문
- hybrid형 {n_hybrid}개: 특정 대상(권리, 절차, 주체 등)을 전체 맥락 속에서 묻는 질문
- 커뮤니티 요약에 나온 내용으로 답할 수 있는 질문만 씀. 역량질문과 겹치지 않게 씀
- 한국어 한 문장, 물음표로 끝냄

[역량질문]
{cqs}

[커뮤니티 요약]
{summaries}

JSON으로만 답하라: {{"global": ["질문", ...], "hybrid": ["질문", ...]}}
"""


def load_summaries(driver) -> list[str]:
    records = driver.execute_query(
        "MATCH (c:Community) WHERE c.summary IS NOT NULL "
        "RETURN c.summary AS s ORDER BY c.size DESC LIMIT $n", n=MAX_SUMMARIES).records
    return [r["s"] for r in records]


def write_draft(cqs: list[dict], extra: dict[str, list[str]]) -> None:
    rows = [(c["id"], c["question"], "local", "역량질문") for c in cqs]
    rows += [(f"G{i}", q, "global", "LLM 작성") for i, q in enumerate(extra["global"][:N_GLOBAL], 1)]
    rows += [(f"H{i}", q, "hybrid", "LLM 작성") for i, q in enumerate(extra["hybrid"][:N_HYBRID], 1)]
    lines = [
        "# GraphRAG 검색 시험 질문",
        "",
        "- 상태: 검토 중",
        f"- 작성: {date.today()}, 역량질문 {len(cqs)}개 + LLM({schema_common.MODEL_NAME})이 쓴 Global형 {N_GLOBAL}개·Hybrid형 {N_HYBRID}개",
        "- 기대 검색 방식: 역량질문은 특정 개념·요건·기간을 묻는 질문이라 local, 나머지는 작성 의도대로 붙임",
        "- 확정 방법: 질문·기대 검색 방식을 검토해 고친 뒤 위 상태를 `상태: 확정`으로 바꿈",
        "",
        "| ID | 질문 | 기대 검색 방식 | 출처 |",
        "|---|---|---|---|",
    ]
    lines += [f"| {i} | {q} | {r} | {s} |" for i, q, r, s in rows]
    TEST_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    schema_common.setup_console()
    driver = graph_search.precheck()
    try:
        cqs = schema_common.load_competency_questions()
        prompt = PROMPT.format(
            global_desc=graph_search.ROUTE_CRITERIA["global"], hybrid_desc=graph_search.ROUTE_CRITERIA["hybrid"],
            n_global=N_GLOBAL, n_hybrid=N_HYBRID,
            cqs="\n".join(f"- {c['id']}: {c['question']}" for c in cqs),
            summaries="\n".join(f"- {s}" for s in load_summaries(driver)),
        )
        extra = await schema_common.ask_json(schema_common.make_llm(reasoning_effort="medium"), prompt)
    finally:
        driver.close()
    write_draft(cqs, extra)
    print(f"[시험 질문] {TEST_FILE.name} 작성(상태: 검토 중) — 검토 후 `상태: 확정`으로 바꿀 것")
    print(TEST_FILE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
