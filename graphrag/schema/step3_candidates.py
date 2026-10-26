"""3단계: 스키마 후보 넓게 뽑기.

표본 청크마다 `SchemaFromTextExtractor.run(text=청크 본문, examples=역량질문 문자열)`을 따로 호출함.
- 모든 호출에 같은 역량질문 문자열만 넣음
- 앞 호출의 추출 결과를 다음 호출 입력에 넣지 않음(앞 결과를 따라 써서 후보가 줄어드는 것을 막음)

출력: candidates/sample-{chunk_index}.json (호출별 LLM 원본 응답 + GraphSchema 변환 결과)

실행:
    python step3_candidates.py          # 이미 있는 결과는 건너뜀
    python step3_candidates.py --force  # 모두 다시 호출
"""

from __future__ import annotations

import argparse
import asyncio
import json

from neo4j_graphrag.components.schema import SchemaFromTextExtractor
from neo4j_graphrag.llm.types import LLMResponse

from common import (
    CANDIDATES_DIR,
    MODEL_NAME,
    SAMPLES_FILE,
    examples_string,
    load_chunks,
    load_competency_questions,
    make_llm,
    precheck,
    setup_console,
    write_json,
)

CONCURRENCY = 4
RETRIES = 3


def make_recording_llm():
    """LLM 원본 응답을 남기려고 ainvoke 결과를 기록하는 OpenAILLM."""
    llm = make_llm(reasoning_effort="medium")
    original = llm.ainvoke
    llm.raw_contents = []  # type: ignore[attr-defined]

    async def ainvoke(*args, **kwargs) -> LLMResponse:
        res = await original(*args, **kwargs)
        llm.raw_contents.append(res.content)  # type: ignore[attr-defined]
        return res

    llm.ainvoke = ainvoke  # type: ignore[method-assign]
    return llm


async def extract_one(chunk: dict, examples: str, sem: asyncio.Semaphore, force: bool) -> str:
    out = CANDIDATES_DIR / f"sample-{chunk['chunk_index']}.json"
    if out.exists() and not force:
        return f"  건너뜀 sample-{chunk['chunk_index']}"
    async with sem:
        last_err = None
        for attempt in range(1, RETRIES + 1):
            # 호출마다 새 LLM·추출기를 만듦: 앞 호출의 상태나 결과가 섞이지 않게 함
            llm = make_recording_llm()
            extractor = SchemaFromTextExtractor(llm=llm)
            try:
                schema = await extractor.run(text=chunk["text"], examples=examples)
            except Exception as e:  # LLM 응답 형식 오류 등
                last_err = e
                continue
            raw = llm.raw_contents[-1] if llm.raw_contents else ""
            try:
                raw_json = json.loads(raw)
            except json.JSONDecodeError:
                raw_json = raw
            write_json(out, {
                "chunk_index": chunk["chunk_index"],
                "model": MODEL_NAME,
                "attempt": attempt,
                "llm_raw": raw_json,
                "graph_schema": schema.model_dump(mode="json"),
            })
            n, r, p = len(schema.node_types), len(schema.relationship_types), len(schema.patterns)
            return f"  완료 sample-{chunk['chunk_index']}: 노드 {n}, 관계 {r}, 패턴 {p}"
        return f"  실패 sample-{chunk['chunk_index']}: {type(last_err).__name__}: {last_err}"


async def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    precheck()

    examples = examples_string(load_competency_questions())
    samples = json.loads(SAMPLES_FILE.read_text(encoding="utf-8"))["samples"]
    by_idx = {c["chunk_index"]: c for c in load_chunks()}
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(extract_one(by_idx[s["chunk_index"]], examples, sem, args.force) for s in samples))
    print("\n".join(results))
    failed = [r for r in results if r.strip().startswith("실패")]
    print(f"[완료] 표본 {len(samples)}건 중 실패 {len(failed)}건 → {CANDIDATES_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
