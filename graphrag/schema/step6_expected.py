"""6단계: 기대 추출 결과 만들기(LLM 초안 → 사람 확정).

시험용 청크마다 확정된 스키마 초안으로 뽑혀야 할 노드(타입, 이름)와 관계(시작, 관계 타입, 끝)를
LLM으로 초안 작성함. 역량질문과 관계없는 청크의 기대 결과는 "없음"(빈 목록)으로 둠.
사람이 확인한 뒤 status를 "확정"으로 바꿈(LLM이 쓴 기대 결과로 LLM 추출을 채점하지 않기 위함).

- aliases: 같은 대상을 가리키는 것으로 사람이 인정한 다른 이름. 채점 때 name과 같은 것으로 봄

출력: fill-test-expected.json(status: 초안)

실행:
    python step6_expected.py          # 확정된 파일이 있으면 덮어쓰지 않음
    python step6_expected.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from common import (
    DRAFT_FILE,
    EXPECTED_FILE,
    SAMPLES_FILE,
    ask_json,
    load_chunks,
    load_json_confirmed,
    make_llm,
    precheck,
    setup_console,
    write_json,
)

PROMPT = """[목표]
그래프 스키마 채우기 시험의 정답지를 만들기 위하여, 아래 특허법 청크에서 스키마대로 뽑혀야 할 노드와 관계를 빠짐없이 적음

[처리]
1. 스키마의 노드 타입·관계 타입 설명(추출 기준, 제외 기준, name 규칙)을 그대로 따름. 설명에서 제외한 것은 적지 않음
2. 청크 본문에 근거가 있는 것만 적음. 다른 조문 지식으로 보태지 않음
3. 노드 name은 청크 원문 표현 그대로(name 규칙대로 조사·괄호 속 한자·호 번호만 뺌)
4. 관계는 스키마 패턴에 있는 (시작 타입, 관계 타입, 끝 타입)만 씀. 시작·끝 노드는 nodes에 있어야 함
5. 노드마다 aliases에 같은 대상을 가리키는 짧은 이름 변형을 0 ~ 2개 둠(예: 조사 유무, '또는' 앞뒤를 줄인 표현). 뜻이 달라지는 변형은 넣지 않음
6. 관계와 이어지지 않는 노드는 적지 않음(스키마 설명상 혼자 뽑지 않는 타입이 있음)

[출력]
JSON 객체 하나만 출력:
{{"nodes": [{{"type": "노드 타입", "name": "이름", "aliases": ["변형"]}}],
  "relationships": [{{"start": {{"type": "노드 타입", "name": "이름"}}, "type": "관계 타입", "end": {{"type": "노드 타입", "name": "이름"}}}}]}}

<스키마>
{schema}
</스키마>

<청크 번호="{idx}">
{text}
</청크>
"""


def check(exp: dict, schema: dict) -> list[str]:
    """기대 결과가 스키마·노드 목록과 맞는지 검사."""
    labels = {n["label"] for n in schema["node_types"]}
    pats = {(p["source"], p["relationship"], p["target"]) for p in schema["patterns"]}
    names = {(n["type"], n["name"]) for n in exp["nodes"]}
    errs = [f"스키마에 없는 노드 타입: {n}" for n in exp["nodes"] if n["type"] not in labels]
    for r in exp["relationships"]:
        if (r["start"]["type"], r["type"], r["end"]["type"]) not in pats:
            errs.append(f"패턴에 없는 관계: {r}")
        for end in ("start", "end"):
            if (r[end]["type"], r[end]["name"]) not in names:
                errs.append(f"nodes에 없는 관계 끝점: {r[end]}")
    return errs


async def draft_one(llm, chunk: dict, schema: dict, schema_text: str) -> dict:
    prompt = PROMPT.format(schema=schema_text, idx=chunk["chunk_index"], text=chunk["text"])
    for _ in range(3):
        exp = await ask_json(llm, prompt)
        exp.setdefault("nodes", [])
        exp.setdefault("relationships", [])
        if not check(exp, schema):
            return exp
    raise RuntimeError(f"chunk {chunk['chunk_index']} 기대 결과 검사 실패: {check(exp, schema)}")


async def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    precheck()
    if EXPECTED_FILE.exists() and not args.force:
        if json.loads(EXPECTED_FILE.read_text(encoding="utf-8")).get("status") == "확정":
            sys.exit(f"[건너뜀] {EXPECTED_FILE.name}이(가) 이미 확정됨. 다시 만들려면 --force")

    draft = load_json_confirmed(DRAFT_FILE, "스키마 초안")
    schema = draft["schema"]
    schema_text = json.dumps(
        {k: schema[k] for k in ("node_types", "relationship_types", "patterns")}, ensure_ascii=False, indent=1)
    tests = json.loads(SAMPLES_FILE.read_text(encoding="utf-8"))["test"]
    by_idx = {c["chunk_index"]: c for c in load_chunks()}

    llm = make_llm(reasoning_effort="medium", max_tokens=16000, json_mode=False)
    related = [t for t in tests if t["kind"] == "역량질문 관련"]
    drafts = await asyncio.gather(*(draft_one(llm, by_idx[t["chunk_index"]], schema, schema_text) for t in related))
    by_related = {t["chunk_index"]: d for t, d in zip(related, drafts)}

    chunks = []
    for t in tests:
        if t["kind"] == "역량질문 관련":
            d = by_related[t["chunk_index"]]
            chunks.append({**t, "expected": "있음", "nodes": d["nodes"], "relationships": d["relationships"]})
        else:
            chunks.append({**t, "expected": "없음", "nodes": [], "relationships": []})
    data = {
        "status": "초안",
        "schema_round": draft["round"],
        "match_rule": "노드: 타입과 이름이 같으면 일치. 관계: 시작(타입, 이름)·관계 타입·끝(타입, 이름)이 같으면 일치. "
                      "이름 비교 전 공백·문장부호(ㆍ·,.「」“”'\"()) 제거. aliases에 있는 이름도 같은 이름으로 봄",
        "chunks": chunks,
    }
    write_json(EXPECTED_FILE, data)
    n = sum(len(c["nodes"]) for c in chunks)
    r = sum(len(c["relationships"]) for c in chunks)
    print(f"[완료] 기대 추출 결과 초안: 시험용 {len(chunks)}건, 노드 {n}, 관계 {r} → {EXPECTED_FILE.name}")


if __name__ == "__main__":
    asyncio.run(main())
