"""2단계: 표본(schema 후보 추출용)과 시험용(채우기 시험용) 청크 나누기.

- 장 묶기: 청크 본문의 장 제목(`제N장`)으로 묶고, 제목이 없으면 바로 앞 청크의 장을 따름
- 시험용: 확정된 역량질문의 '답이 있는 대표 조문' 중 첫 조문이 있는 청크(질문마다 1건, 중복 제거)
          + 역량질문과 관계없는 청크(UNRELATED_CHUNKS)
- 표본: 시험용을 뺀 나머지에서 장마다 1건 + 무작위 추가로 SAMPLE_SIZE건(난수 시드 고정)

실행:
    python step2_split.py
"""

from __future__ import annotations

import random
import re
import sys

from common import (
    CQ_FILE,
    SAMPLES_FILE,
    SEED,
    assign_chapters,
    load_chunks,
    load_competency_questions,
    precheck,
    require_confirmed,
    setup_console,
    write_json,
)

SAMPLE_SIZE = 20  # 10 ~ 30건
# 역량질문과 관계없는 청크(기대 추출 결과 "없음"): 지어냄(hallucination) 측정용
UNRELATED_CHUNKS = {
    15: "제28조의3·4 전자문서에 의한 절차 수행과 이용신고(역량질문과 무관한 서류 제출 방식)",
    180: "제218조 ~ 제222조 서류 송달·공시송달·특허공보(역량질문과 무관한 보칙)",
}


def cq_answer_articles() -> dict[str, str]:
    """확정 역량질문 표의 4번째 열(답이 있는 대표 조문)에서 첫 조문 번호를 읽음."""
    text = require_confirmed(CQ_FILE, "역량질문")
    out = {}
    for cq_id, loc in re.findall(r"^\|\s*(CQ\d+)\s*\|[^|]+\|[^|]+\|\s*([^|]+?)\s*\|", text, flags=re.M):
        m = re.search(r"제\d+조(?:의\d+)?", loc)
        if m:
            out[cq_id] = m.group(0)
    return out


def find_article_chunk(chunks: list[dict], article: str) -> int:
    """조문 제목(`제N조(`)이 줄 맨 앞에 나오는 첫 청크."""
    pat = re.compile(rf"(?m)^{re.escape(article)}\(")
    for c in chunks:
        if pat.search(c["text"]):
            return c["chunk_index"]
    raise ValueError(f"{article}이(가) 있는 청크를 찾지 못함")


def main() -> None:
    setup_console()
    precheck(need_llm=False)
    cqs = load_competency_questions()
    chunks = load_chunks()
    chapters = assign_chapters(chunks)

    # 시험용
    test: dict[int, dict] = {}
    for cq_id, article in cq_answer_articles().items():
        idx = find_article_chunk(chunks, article)
        test.setdefault(idx, {"chunk_index": idx, "chapter": chapters[idx], "kind": "역량질문 관련", "reason": []})
        test[idx]["reason"].append(f"{cq_id}({article})")
    for idx, reason in UNRELATED_CHUNKS.items():
        test[idx] = {"chunk_index": idx, "chapter": chapters[idx], "kind": "역량질문 무관", "reason": [reason]}
    if not 5 <= len(test) <= 10:
        sys.exit(f"[중단] 시험용 청크가 {len(test)}건임(5 ~ 10건이어야 함)")

    # 표본: 장마다 1건 + 나머지 무작위
    rng = random.Random(SEED)
    pool = [c["chunk_index"] for c in chunks if c["chunk_index"] not in test]
    by_ch: dict[str, list[int]] = {}
    for idx in pool:
        by_ch.setdefault(chapters[idx], []).append(idx)
    sample = {rng.choice(items) for items in by_ch.values()}
    rest = [i for i in pool if i not in sample]
    sample |= set(rng.sample(rest, SAMPLE_SIZE - len(sample)))
    assert not sample & set(test), "표본과 시험용이 겹침"

    data = {
        "seed": SEED,
        "rule": {
            "chapter": "청크 본문의 장 제목(제N장)으로 묶음. 제목이 없으면 바로 앞 청크의 장. 한 청크에 제목이 여럿이면 마지막 제목",
            "test": "역량질문마다 대표 조문 첫 번째가 있는 청크 + 역량질문과 관계없는 청크 2건",
            "sample": f"시험용을 뺀 청크에서 장마다 1건 + 무작위 추가, 모두 {SAMPLE_SIZE}건",
        },
        "competency_questions": len(cqs),
        "samples": [{"chunk_index": i, "chapter": chapters[i]} for i in sorted(sample)],
        "test": [test[i] for i in sorted(test)],
    }
    write_json(SAMPLES_FILE, data)
    print(f"[완료] 표본 {len(sample)}건, 시험용 {len(test)}건(무관 {len(UNRELATED_CHUNKS)}건) → {SAMPLES_FILE}")
    for s in data["samples"]:
        print(f"  표본 {s['chunk_index']:>3}  {s['chapter']}")
    for t in data["test"]:
        print(f"  시험 {t['chunk_index']:>3}  {t['kind']}  {', '.join(t['reason'])}")


if __name__ == "__main__":
    main()
