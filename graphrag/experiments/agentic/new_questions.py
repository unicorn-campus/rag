"""검색 재비교용 새 질문 20개와 채점 요점 초안 만들기.

기존 시험 질문 13개(graphrag/search/test-questions.md)는 ReAct·워크플로우를 고칠 때 이미 본 질문이라,
재비교에는 새 질문을 씀(윤큐 권고). 주제와 근거 조문은 사람이 정하고(아래 SPECS), 질문 문장과 요점은
LLM이 그 조문 원문만 보고 초안을 씀. 이후 QA 검토(원문 대조)를 거쳐 확정본으로 씀.

유형: single(조문 하나), multi(여러 조문을 묶어야 함), flow(절차 흐름)

출력: new-questions.json(status: 초안), new-questions.md

실행(저장소 루트에서):
    python graphrag/experiments/agentic/new_questions.py
"""

from __future__ import annotations

import asyncio
import json

from harness import EXP_DIR, load_chunks_cached
from common import ask_json, make_llm, setup_console, write_json
from cq_keypoints import article_texts

OUT = EXP_DIR / "new-questions.json"
OUT_MD = EXP_DIR / "new-questions.md"

SPECS = [
    ("N01", "single", "특허를 받을 수 있는 권리를 가지는 자", ["제33조"]),
    ("N02", "single", "설정등록을 받으려는 자의 특허료 납부 기한", ["제79조"]),
    ("N03", "single", "특허권자가 가지는 권리(특허권의 효력)", ["제94조"]),
    ("N04", "single", "특허취소신청의 주체·기한·신청처", ["제132조의2"]),
    ("N05", "single", "특허무효심판을 청구할 수 있는 자", ["제133조"]),
    ("N06", "single", "권리범위 확인심판을 청구할 수 있는 자와 목적", ["제135조"]),
    ("N07", "single", "정정심판을 청구할 수 있는 자와 정정할 수 있는 경우", ["제136조"]),
    ("N08", "multi", "재심을 청구할 수 있는 자와 재심청구 기간", ["제178조", "제180조"]),
    ("N09", "single", "심결 등에 대한 소의 관할 법원과 제기 기간", ["제186조"]),
    ("N10", "single", "비밀누설죄의 주체와 형벌", ["제226조"]),
    ("N11", "single", "위증죄의 주체·형벌과 자수 시 감면", ["제227조"]),
    ("N12", "single", "거짓행위의 죄의 대상 행위와 형벌", ["제229조"]),
    ("N13", "single", "양벌규정에서 법인에 부과하는 벌금", ["제230조"]),
    ("N14", "single", "특허권 침해자의 과실 추정", ["제130조"]),
    ("N15", "single", "업무상 신용회복을 위한 조치 청구", ["제131조"]),
    ("N16", "multi", "특허권 침해에 대한 민사상 청구와 형사상 처벌 전체", ["제126조", "제128조", "제225조"]),
    ("N17", "multi", "전용실시권과 통상실시권의 차이(설정·허락, 권리 내용)", ["제100조", "제102조"]),
    ("N18", "single", "등록지연에 따른 특허권 존속기간 연장의 요건", ["제92조의2"]),
    ("N19", "flow", "특허취소신청부터 결정, 그 결정에 대한 불복 소송까지의 흐름", ["제132조의2", "제132조의13", "제186조"]),
    ("N20", "flow", "특허무효심판 청구부터 심판청구서 제출, 심결까지의 흐름", ["제133조", "제140조", "제162조"]),
]

PROMPT = """[목표]
특허법 GraphRAG 검색 시험용 질문 1개와, 그 정답의 요점을 씀. 주어진 조문 원문만 근거로 함

[주제]
{topic}

[조문 원문]
{articles}

[처리]
- 질문: 법을 잘 모르는 교육생이 물을 법한 자연스러운 한국어 한 문장. 조문 번호를 질문에 넣지 않음. 주제의 범위를 넘지 않음
- 요점: 질문에 답하려면 꼭 들어가야 할 사실 2 ~ 5개(주체, 절차, 기한, 처리 기관, 효과, 형량 등). 원문 표현을 살리고 원문에 없는 해석·예외는 쓰지 않음. 요점마다 근거 조문 번호(항까지 알면 항 포함)
- 질문이 묻지 않는 세부(산정 방법, 준용 규정 등)는 요점에 넣지 않음

[출력]
JSON 하나만: {{"question": "질문", "keypoints": [{{"point": "요점", "article": "제N조제M항"}}]}}
"""


async def main() -> None:
    setup_console()
    texts = article_texts(load_chunks_cached(), [a for *_, arts in SPECS for a in arts])
    missing = [a for *_, arts in SPECS for a in arts if a not in texts]
    if missing:
        raise SystemExit(f"[중단] 조문 원문을 찾지 못함: {missing}")
    llm = make_llm(reasoning_effort="medium", max_tokens=8000, json_mode=False)
    drafts = await asyncio.gather(*(ask_json(llm, PROMPT.format(topic=topic, articles="\n\n".join(texts[a] for a in arts)))
                                    for _, _, topic, arts in SPECS))
    items = [{"id": i, "type": t, "topic": topic, "articles": arts, "question": d.get("question", ""),
              "keypoints": d.get("keypoints", [])} for (i, t, topic, arts), d in zip(SPECS, drafts)]
    data = {"status": "초안", "items": items}
    write_json(OUT, data)
    OUT_MD.write_text(render(data), encoding="utf-8")
    print(OUT_MD.read_text(encoding="utf-8"))


def render(data: dict) -> str:
    lines = ["# 검색 재비교용 새 질문 20개", "", f"- 상태: {data['status']}",
             "- 주제·근거 조문은 사람이 정하고, 질문 문장과 요점은 LLM이 조문 원문만 보고 초안을 씀", ""]
    if data.get("review_note"):
        lines += [f"- 검토: {data['review_note']}", ""]
    for it in data["items"]:
        lines += [f"## {it['id']} ({it['type']}) {it['question']}", "", f"- 근거 조문: {', '.join(it['articles'])}"]
        lines += [f"{k + 1}. {p['point']} ({p['article']})" for k, p in enumerate(it["keypoints"])] + [""]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    asyncio.run(main())
