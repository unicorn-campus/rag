"""검색 답변 채점용 요점 체크리스트 초안(LLM 초안 → 사람 확정).

역량질문 9개마다 '답이 있는 대표 조문'(competency-questions.md, 확정본) 원문만 보고 정답의 요점을 2 ~ 6개 씀.
답변 채점 때 요점마다 '답에 들어 있는가'를 예/아니오로 판정해 요점 재현율을 셈(윤큐 제안).

출력: cq-keypoints.json(status: 초안), cq-keypoints.md(사람 검토용)

실행(저장소 루트에서):
    python graphrag/experiments/agentic/cq_keypoints.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys

from harness import EXP_DIR, load_chunks_cached
from common import CQ_FILE, ask_json, make_llm, require_confirmed, setup_console, write_json
from pipelines import norm_article, segments
from harness import Context

KEY_FILE = EXP_DIR / "cq-keypoints.json"
KEY_MD = EXP_DIR / "cq-keypoints.md"
_ROW = re.compile(r"^\|\s*(CQ\d+)\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", re.M)
_REF = re.compile(r"제\s*\d+\s*조(?:의\s*\d+)?")

PROMPT = """[목표]
아래 질문에 대한 정답의 요점을, 주어진 조문 원문만 근거로 2 ~ 6개 씀. 검색 답변 채점에 씀

[질문]
{question}

[조문 원문]
{articles}

[처리]
- 요점 하나는 확인 가능한 사실 하나(주체, 절차, 기한, 처리 기관, 효과, 형량 등)
- 원문 표현을 살리고, 원문에 없는 해석·예외는 쓰지 않음
- 요점마다 근거 조문 번호(항까지 알면 항 포함)를 붙임

[출력]
JSON 하나만: {{"keypoints": [{{"point": "요점", "article": "제N조제M항"}}]}}
"""


def article_texts(chunks: dict[int, str], wanted: list[str]) -> dict[str, str]:
    """대표 조문 번호 → 원문(청크를 조문 조각으로 나눠 모음, 여러 청크에 걸치면 이어 붙임)."""
    ctx = Context.__new__(Context)
    ctx.chunks = chunks
    found: dict[str, str] = {}
    for ci in sorted(chunks):
        for art, body in segments(ctx, ci):
            if art in wanted:
                found[art] = (found.get(art, "") + "\n" + body).strip()
    return found


async def main() -> None:
    setup_console()
    text = require_confirmed(CQ_FILE, "역량질문")
    rows = [(i, q, [norm_article(a) for a in _REF.findall(arts)]) for i, q, arts in _ROW.findall(text)]
    chunks = load_chunks_cached()
    texts = article_texts(chunks, [a for _, _, arts in rows for a in arts])
    missing = [a for _, _, arts in rows for a in arts if a not in texts]
    if missing:
        sys.exit(f"[중단] 대표 조문 원문을 찾지 못함: {missing}")
    llm = make_llm(reasoning_effort="medium", max_tokens=8000, json_mode=False)
    results = await asyncio.gather(*(ask_json(llm, PROMPT.format(
        question=q, articles="\n\n".join(texts[a] for a in arts))) for _, q, arts in rows))
    data = {"status": "초안", "items": [{"id": i, "question": q, "articles": arts, "keypoints": r.get("keypoints", [])}
                                       for (i, q, arts), r in zip(rows, results)]}
    write_json(KEY_FILE, data)
    lines = ["# 역량질문 답변 요점(채점용)", "", "- 상태: 초안", "- 확정 방법: cq-keypoints.json을 검토해 고친 뒤 `\"status\": \"확정\"`으로 바꿈", ""]
    for it in data["items"]:
        lines += [f"## {it['id']}. {it['question']} ({', '.join(it['articles'])})", ""]
        lines += [f"{k + 1}. {p['point']} ({p['article']})" for k, p in enumerate(it["keypoints"])] + [""]
    KEY_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(KEY_MD.read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
