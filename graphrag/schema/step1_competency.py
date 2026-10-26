"""1단계: 역량질문(Competency Question) 초안 작성.

기본 질문 4가지(사람이 확인한 답)와 특허법 표본 청크·조문 제목 목록을 LLM에 주고
질문 유형별 역량질문 초안을 받아 `competency-questions.md`로 저장함(상태: 초안).
사람이 검토해 `상태: 확정`으로 바꾸면 다음 단계에서 읽어 씀.

실행:
    python step1_competency.py          # 초안 생성(확정된 파일이 있으면 덮어쓰지 않음)
    python step1_competency.py --force  # 확정 파일도 덮어씀
"""

from __future__ import annotations

import argparse
import asyncio
import random
import re
import sys

from common import (
    CONFIRMED_MARK,
    CQ_FILE,
    SEED,
    assign_chapters,
    ask_json,
    load_chunks,
    make_llm,
    precheck,
    setup_console,
)

# 사람이 확인한 기본 질문 4가지의 답(Ontology 101 Step 1)
BASIC_ANSWERS = {
    "도메인": "대한민국 특허법(법률 제21134호, 2025. 11. 11. 시행) 본문 제1장 ~ 제12장 조문. 부칙은 범위 밖",
    "용도": "GraphRAG 실습용 조문 관계 질의응답. 벡터 검색만으로 어려운 '누가-무엇을-언제까지-어떤 효과' 연결과 조문 간 참조를 따라가는 다중 단계 질문에 답함",
    "답할 질문 유형": "정의·요건 / 주체·절차 / 기간·기한·금액 / 효력·제한·구제",
    "사용자와 관리자": "사용자: AI Boot Camp 교육생(사내 직원, 법률 비전문가). 관리자: 강사·데이터 엔지니어(스키마와 인덱싱 관리)",
}
QUESTION_TYPES = ["정의·요건", "주체·절차", "기간·기한·금액", "효력·제한·구제"]
SAMPLES_PER_CHAPTER = 2

PROMPT = """[목표]
특허법 지식 그래프의 범위를 정하기 위하여, 아래 기본 질문 답과 표본 문서를 보고 역량 질문(competency question) 목록 작성

[맥락]
- 실제 사용자 질문은 없음. 사용자(법률 비전문가 교육생)가 물을 법한 질문을 표본 문서에 근거해 씀
{basic}

[처리]
1. 질문 유형마다 역량 질문을 2개씩 씀: {types}
2. 표본 문서나 조문 제목 목록으로 보아 특허법 본문에 답이 있는 질문만 남김
3. 질문 하나에는 한 가지만 물음. 구체적 값(사람 이름, 날짜)은 넣지 않고, 일반화된 질문으로 씀
4. 그래프 스키마의 클래스나 타입 이름(예: Person 노드, HAS_PERIOD 관계)은 쓰지 않고 자연스러운 한국어 질문 문장으로만 씀
5. 가능하면 조문 둘 이상을 이어야 답할 수 있는 질문(예: 어떤 처분에 불복하려면 누구에게 언제까지 무엇을 청구하는가)을 유형마다 1개 포함
6. 답이 있는 대표 조문(예: 제29조)을 적음

[출력]
JSON 객체 하나만 출력:
{{"questions": [{{"type": "질문 유형", "question": "역량 질문", "answer_location": "제N조(제목), ..."}}]}}

<조문제목목록>
{titles}
</조문제목목록>

<표본문서>
{samples}
</표본문서>
"""


def article_titles(chunks: list[dict]) -> str:
    """청크 본문에서 조문 제목(제N조(제목))을 순서대로 중복 없이 뽑음."""
    seen, out = set(), []
    for c in chunks:
        for m in re.findall(r"(?m)^(제\d+조(?:의\d+)?\([^)]+\))", c["text"]):
            if m not in seen:
                seen.add(m)
                out.append(m)
    return "\n".join(out)


def pick_samples(chunks: list[dict]) -> str:
    """장마다 SAMPLES_PER_CHAPTER건씩 무작위 표본(시드 고정)."""
    chapters = assign_chapters(chunks)
    by_ch: dict[str, list[dict]] = {}
    for c in chunks:
        by_ch.setdefault(chapters[c["chunk_index"]], []).append(c)
    rng = random.Random(SEED)
    parts = []
    for ch, items in by_ch.items():
        for c in rng.sample(items, min(SAMPLES_PER_CHAPTER, len(items))):
            parts.append(f"[{ch} / chunk {c['chunk_index']}]\n{c['text']}")
    return "\n\n".join(parts)


def render_md(questions: list[dict]) -> str:
    basic = "\n".join(f"| {k} | {v} |" for k, v in BASIC_ANSWERS.items())
    rows = "\n".join(
        f"| CQ{i:02d} | {q['type']} | {q['question']} | {q.get('answer_location', '')} |"
        for i, q in enumerate(questions, 1)
    )
    return f"""# 역량질문(Competency Question)

- 상태: 초안
- 작성: LLM({'openai/gpt-oss-120b'}) 초안 → 사람 검토 후 확정. 확정하면 위 줄을 `{CONFIRMED_MARK}`으로 바꿈
- 역량질문: 그래프가 **답할 수 있어야 하는 질문 목록**. 스키마 범위를 정하고, 완성 후 시금석(litmus test, 검증 기준)으로 씀
- 근거: `references/how-to-competency-question.md`(Ontology 101 Step 1)

## 1. 기본 질문 4가지

| 기본 질문 | 답 |
|---|---|
{basic}

## 2. 역량질문 목록

| ID | 질문 유형 | 역량질문 | 답이 있는 대표 조문 |
|---|---|---|---|
{rows}
"""


async def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    precheck()
    if CQ_FILE.exists() and CONFIRMED_MARK in CQ_FILE.read_text(encoding="utf-8") and not args.force:
        sys.exit(f"[건너뜀] {CQ_FILE.name}이(가) 이미 확정됨. 다시 만들려면 --force")

    chunks = load_chunks()
    prompt = PROMPT.format(
        basic="\n".join(f"- {k}: {v}" for k, v in BASIC_ANSWERS.items()),
        types=", ".join(QUESTION_TYPES),
        titles=article_titles(chunks),
        samples=pick_samples(chunks),
    )
    llm = make_llm(reasoning_effort="medium")
    data = await ask_json(llm, prompt)
    questions = sorted(data["questions"], key=lambda q: QUESTION_TYPES.index(q["type"]) if q["type"] in QUESTION_TYPES else 99)
    CQ_FILE.write_text(render_md(questions), encoding="utf-8")
    print(f"[완료] 역량질문 초안 {len(questions)}개 → {CQ_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
