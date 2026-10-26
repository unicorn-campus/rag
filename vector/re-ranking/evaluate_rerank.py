"""Re-ranking 효과와 1차 검색 후보 수(FETCH_K) 실험.

정답 청크를 아는 질문 세트로 다음을 비교함 (모두 Top-K 기준 Hit@K, MRR@K).
- 벡터만            : Naive RAG
- Hybrid (RRF)      : hybrid-search 예제 (Re-ranking 없음)
- Hybrid + Rerank   : 이 예제

API 호출을 아끼려고 질문마다 BM25·벡터 검색을 최대 후보 수로 한 번, Re-ranking을
최대 후보 전체에 대해 한 번만 함. Cohere Re-ranker 점수는 질문-청크 쌍마다 따로
계산되므로(후보 묶음이 달라도 같은 청크의 점수는 같음) 더 적은 후보 수의 결과는
캐시된 점수로 다시 정렬해 계산함. LLM은 호출하지 않음.

실행:
    python evaluate_rerank.py
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time

from cohere.errors import TooManyRequestsError

import rerank_rag as rag

# (질문 유형, 질문, 정답 청크 chunk_index) - hybrid-search/evaluate_weights.py와 같은 세트
EVAL_SET: list[tuple[str, str, set[int]]] = [
    ("keyword", "특허법 제42조 출원 방법", {28}),
    ("keyword", "제133조", {117}),
    ("keyword", "제225조의 내용은?", {186, 187}),
    ("keyword", "특허권의 존속기간은?", {73}),
    ("keyword", "특허를 받을 수 없는 발명은?", {21}),
    ("keyword", "정정심판", {124}),
    ("keyword", "통상실시권이란?", {86}),
    ("keyword", "전용실시권 설정", {84}),
    ("keyword", "출원공개는 언제 되나요?", {55}),
    ("keyword", "특허출원심사의 청구 기간", {51}),
    ("keyword", "분할출원 요건", {36}),
    ("keyword", "특허취소신청 사유", {109}),
    ("keyword", "거절이유통지를 받으면 어떻게 하나요?", {53}),
    ("keyword", "특허권의 효력 범위", {81}),
    ("natural", "특허를 받을 수 있는 조건은 ?", {17}),
    ("natural", "같은 발명을 다른 날 두 사람이 출원하면 누가 특허를 받나요?", {23}),
    ("natural", "논문으로 먼저 발표한 발명도 특허를 받을 수 있나요?", {20}),
    ("natural", "남의 특허를 허락 없이 쓰면 어떤 처벌을 받나요?", {186, 187}),
    ("natural", "미성년자도 특허출원을 할 수 있나요?", {1}),
    ("natural", "의약품 허가 때문에 특허를 못 쓴 기간만큼 보호 기간을 늘릴 수 있나?", {73}),
    ("natural", "외국에 먼저 출원한 경우 그 날짜를 인정받을 수 있나요?", {43}),
    ("natural", "특허 등록하려면 돈을 언제 내야 하나요?", {61}),
    ("natural", "특허를 침해당하면 손해를 배상받을 수 있나?", {103}),
    ("natural", "잘못 등록된 특허를 없애 달라고 하려면?", {117}),
    ("natural", "누가 내 특허를 베끼고 있으면 못 하게 막을 수 있나?", {100}),
    ("natural", "발명이란 무엇인가?", {0}),
]
ARTICLE_SAMPLE = 30
SEED = 42
QTYPES = ("keyword", "natural", "article")
FETCH_KS = (4, 10, 20, 30)
RATE_LIMIT_WAIT_SEC = 60  # Cohere 트라이얼 키 호출 한도
_ARTICLE_HEAD = re.compile(r"(?:^|\n)(제\d+조(?:의\d+)?)\(")


def article_set(docs: list, n: int = ARTICLE_SAMPLE, seed: int = SEED) -> list[tuple[str, str, set[int]]]:
    """조문 n개를 고정 시드로 무작위 추출해 "제N조의 내용은?" 질문을 만듦."""
    heads: dict[str, set[int]] = {}
    for d in docs:
        for art in _ARTICLE_HEAD.findall(d.page_content):
            heads.setdefault(art, set()).add(d.metadata["chunk_index"])
    picked = random.Random(seed).sample(sorted(heads), min(n, len(heads)))
    return [("article", f"{art}의 내용은?", heads[art]) for art in picked]


def with_retry(fn, *args, retries: int = 5, **kwargs):
    """Cohere 호출 한도(429)에 걸리면 1분 쉬고 다시 호출함."""
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except TooManyRequestsError:
            if attempt == retries:
                raise
            print(f"[rate-limit] Cohere 호출 한도 초과 → {RATE_LIMIT_WAIT_SEC}초 대기 후 재시도", flush=True)
            time.sleep(RATE_LIMIT_WAIT_SEC)


def first_hit_rank(docs: list, gold: set[int], k: int) -> int | None:
    for rank, d in enumerate(docs[:k], 1):
        if d.metadata.get("chunk_index") in gold:
            return rank
    return None


def summarize(ranks: dict[str, list[int | None]]) -> dict[str, tuple[float, float]]:
    """질문 유형별 (Hit, MRR)."""
    return {name: (sum(r is not None for r in rs) / len(rs), sum(1 / r for r in rs if r) / len(rs))
            for name, rs in ranks.items()}


def collect(bm25, vector, reranker, fuse, questions, max_fetch: int) -> list[dict]:
    """질문마다 1차 검색(최대 후보 수)과 Re-ranking 점수를 한 번씩 구해 캐시함."""
    cached = []
    for i, (qtype, q, gold) in enumerate(questions, 1):
        bm25_docs = bm25.invoke(q)[:max_fetch]
        vector_docs = with_retry(vector.invoke, q)[:max_fetch]
        pool = fuse([bm25_docs, vector_docs])
        results = with_retry(reranker.rerank, pool, q, top_n=None)
        scores = {pool[r["index"]].metadata["chunk_index"]: r["relevance_score"] for r in results}
        cached.append({"type": qtype, "gold": gold, "bm25": bm25_docs, "vector": vector_docs,
                       "scores": scores})
        print(f"\r[collect] {i}/{len(questions)}", end="", flush=True)
    print()
    return cached


def evaluate(cached: list[dict], fuse, fetch_ks=FETCH_KS, k: int = rag.TOP_K) -> dict[str, dict]:
    """방법별 → 질문 유형별 (Hit@k, MRR@k).

    fuse: EnsembleRetriever.weighted_reciprocal_rank (캐시된 검색 결과를 가중 RRF로 결합)
    """
    report: dict[str, dict] = {}

    def add(name: str, ranks_by_question: list[int | None]) -> None:
        ranks: dict[str, list[int | None]] = {"all": []}
        for c, r in zip(cached, ranks_by_question):
            ranks["all"].append(r)
            ranks.setdefault(c["type"], []).append(r)
        report[name] = summarize(ranks)

    add("벡터만", [first_hit_rank(c["vector"], c["gold"], k) for c in cached])
    for fk in fetch_ks:
        hybrid_ranks, rerank_ranks = [], []
        for c in cached:
            pool = fuse([c["bm25"][:fk], c["vector"][:fk]])
            hybrid_ranks.append(first_hit_rank(pool, c["gold"], k))
            reranked = sorted(pool, key=lambda d: c["scores"][d.metadata["chunk_index"]], reverse=True)
            rerank_ranks.append(first_hit_rank(reranked, c["gold"], k))
        add(f"Hybrid (후보 {fk}+{fk})", hybrid_ranks)
        add(f"Hybrid+Rerank (후보 {fk}+{fk})", rerank_ranks)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-ranking 효과 / 후보 수 실험")
    parser.parse_args(argv)

    rag.load_env()
    store = rag.load_vectorstore()
    max_fetch = max(FETCH_KS)
    bm25 = rag.load_bm25_retriever(k=max_fetch)
    rag.check_same_corpus(store, bm25)
    vector = rag.build_vector_retriever(store, max_fetch)
    reranker = rag.build_reranker()
    fuse = rag.build_ensemble(bm25, vector).weighted_reciprocal_rank

    questions = EVAL_SET + article_set(bm25.docs)
    counts = ", ".join(f"{t} {sum(q[0] == t for q in questions)}" for t in QTYPES)
    print(f"질문 {len(questions)}개 ({counts}), Re-ranker {rag.RERANK_MODEL}, "
          f"BM25:벡터 = {rag.BM25_WEIGHT}:{1 - rag.BM25_WEIGHT:.1f}, Top-{rag.TOP_K} 기준 Hit / MRR")
    report = evaluate(collect(bm25, vector, reranker, fuse, questions, max_fetch), fuse)

    header = f"{'방법':<24}|" + "|".join(f"{name:^14}" for name in ("all",) + QTYPES)
    print("\n" + header)
    print("-" * (len(header) + 6))
    for name, m in report.items():
        cells = "|".join(f" {m[n][0]:>5.0%} {m[n][1]:>6.3f} " for n in ("all",) + QTYPES)
        print(f"{name:<24}|{cells}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
