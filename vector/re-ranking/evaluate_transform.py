"""질문 변환(--transform) 있음 / 없음 비교 실험.

evaluate_rerank.py와 같은 질문 56개로, 같은 Re-ranking 파이프라인에서
질문 변환만 켜고 끈 결과를 Top-K 기준 Hit@K, MRR@K로 비교함.

Cohere 트라이얼 키 한도(월 1,000회, Re-ranking 분당 10회)를 고려해 호출을 최소화함.
- 임베딩: 모든 질문의 모든 질의를 한 번에 묶어 호출 (embed는 호출당 최대 96개)
- Re-ranking: 질문당 최대 2회
    ① 원 질문으로 '변환 후 후보 전체'를 채점 → 변환 없음 결과는 이 점수로 원 질문 후보만 정렬
      (Re-ranker 점수는 질문-청크 쌍마다 독립이므로 원래 파이프라인과 같은 결과)
    ② rerank_query(원 질문 + 변환 질의)로 채점 → 변환 있음 결과 (technique이 none이면 ①과 같아 생략)
- 1차 검색 결과는 EnsembleRetriever.invoke()와 같은 방식(BM25 + 벡터 → 가중 RRF)으로 계산함

실행:
    python evaluate_transform.py
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter

import evaluate_rerank as ev
import rerank_rag as rag

EMBED_BATCH = 96         # Cohere embed 호출당 최대 텍스트 수
RERANK_INTERVAL_SEC = 6.5  # Re-ranking 분당 10회 한도 → 호출 간격을 두어 429를 피함


class Throttle:
    """연속 호출 사이에 최소 간격을 둠."""

    def __init__(self, interval: float):
        self.interval, self.last = interval, 0.0

    def wait(self) -> None:
        if (delay := self.last + self.interval - time.monotonic()) > 0:
            time.sleep(delay)
        self.last = time.monotonic()


def make_plans(transformer, questions) -> list[rag.QueryPlan]:
    """질문마다 LLM으로 질문 변환 계획을 만듦 (Groq, Cohere 호출 없음)."""
    plans = []
    for i, (_, q, _) in enumerate(questions, 1):
        plans.append(transformer.invoke(q))
        print(f"\r[transform] {i}/{len(questions)}", end="", flush=True)
    print()
    return plans


def first_stage_results(texts: list[str], store, bm25, fuse) -> dict[str, list]:
    """질의별 1차 검색 결과 (BM25 + 벡터 → 가중 RRF). 임베딩은 묶어서 호출함."""
    embeddings = store.embeddings
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start:start + EMBED_BATCH]
        vectors += ev.with_retry(embeddings.embed, batch, input_type="search_query")
    print(f"[embed] 질의 {len(texts)}개 → Cohere 호출 {-(-len(texts) // EMBED_BATCH)}회")
    return {
        text: fuse([bm25.invoke(text), store.similarity_search_by_vector(vec, k=rag.FETCH_K)])
        for text, vec in zip(texts, vectors)
    }


def score_map(reranker, throttle, docs, query) -> dict[int, float]:
    throttle.wait()
    results = ev.with_retry(reranker.rerank, docs, query, top_n=None)
    return {docs[r["index"]].metadata["chunk_index"]: r["relevance_score"] for r in results}


def top_by_score(docs, scores, k):
    return sorted(docs, key=lambda d: scores[d.metadata["chunk_index"]], reverse=True)[:k]


def evaluate(questions, plans, store, bm25, reranker, fuse, k: int = rag.TOP_K) -> list[dict]:
    """질문별 변환 없음 / 있음의 정답 순위를 구함."""
    texts = list(dict.fromkeys(
        q for (_, question, _), plan in zip(questions, plans) for q in rag.search_queries(question, plan)))
    first = first_stage_results(texts, store, bm25, fuse)

    throttle, rows, calls = Throttle(RERANK_INTERVAL_SEC), [], 0
    for i, ((qtype, question, gold), plan) in enumerate(zip(questions, plans), 1):
        original_pool = first[question]
        pool = rag.merge_candidates([first[q] for q in rag.search_queries(question, plan)])

        original_scores = score_map(reranker, throttle, pool, question)
        calls += 1
        if plan.technique == "none":
            transformed_scores = original_scores
        else:
            transformed_scores = score_map(reranker, throttle, pool, rag.rerank_query(question, plan))
            calls += 1

        before = top_by_score(original_pool, original_scores, k)
        after = top_by_score(pool, transformed_scores, k)
        rows.append({"type": qtype, "question": question, "plan": plan,
                     "pool": (len(original_pool), len(pool)),
                     "before": ev.first_hit_rank(before, gold, k),
                     "after": ev.first_hit_rank(after, gold, k)})
        print(f"\r[rerank] {i}/{len(questions)}", end="", flush=True)
    print(f"\n[rerank] Cohere 호출 {calls}회")
    return rows


def report(rows: list[dict], k: int = rag.TOP_K) -> None:
    def metrics(key, subset):
        ranks = [r[key] for r in subset]
        return sum(x is not None for x in ranks) / len(ranks), sum(1 / x for x in ranks if x) / len(ranks)

    print(f"\n[1] 변환 없음 vs 있음 (Top-{k} 기준 Hit / MRR)")
    print(f"{'질문 유형':<10}| {'개수':>4} | {'변환 없음':^15} | {'변환 있음':^15}")
    print("-" * 56)
    for name in ("all",) + ev.QTYPES:
        subset = rows if name == "all" else [r for r in rows if r["type"] == name]
        if not subset:
            continue
        (h0, m0), (h1, m1) = metrics("before", subset), metrics("after", subset)
        print(f"{name:<10}| {len(subset):>4} | {h0:>5.0%}  {m0:>6.3f} | {h1:>5.0%}  {m1:>6.3f}")

    print("\n[2] LLM이 고른 기법별 결과 (MRR 변환 없음 → 있음, 순위가 좋아짐/나빠짐)")
    by_tech: dict[str, list[dict]] = {}
    for r in rows:
        by_tech.setdefault(r["plan"].technique, []).append(r)
    rr = lambda x: 1 / x if x else 0.0  # noqa: E731
    for tech, subset in sorted(by_tech.items(), key=lambda t: -len(t[1])):
        types = Counter(r["type"] for r in subset)
        m0 = sum(rr(r["before"]) for r in subset) / len(subset)
        m1 = sum(rr(r["after"]) for r in subset) / len(subset)
        up = sum(rr(r["after"]) > rr(r["before"]) for r in subset)
        down = sum(rr(r["after"]) < rr(r["before"]) for r in subset)
        print(f"  {tech:<14} {len(subset):>2}개 {dict(types)}  MRR {m0:.3f} → {m1:.3f}  (좋아짐 {up}, 나빠짐 {down})")

    print("\n[3] 순위가 바뀐 질문")
    fmt = lambda x: f"{x}위" if x else "없음"  # noqa: E731
    changed = [r for r in rows if r["before"] != r["after"]]
    for r in changed:
        print(f"  [{r['plan'].technique}] {r['question']}  {fmt(r['before'])} → {fmt(r['after'])}"
              f"  (후보 {r['pool'][0]} → {r['pool'][1]}개)")
        for q in r["plan"].queries:
            print(f"      · {q[:70]}")
    if not changed:
        print("  없음")


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="질문 변환 있음/없음 비교").parse_args(argv)

    rag.load_env()
    store = rag.load_vectorstore()
    bm25 = rag.load_bm25_retriever(k=rag.FETCH_K)
    rag.check_same_corpus(store, bm25)
    fuse = rag.build_ensemble(bm25, rag.build_vector_retriever(store)).weighted_reciprocal_rank
    reranker = rag.build_reranker()
    transformer = rag.build_transformer(rag.build_llm(max_retries=6))

    questions = ev.EVAL_SET + ev.article_set(bm25.docs)
    print(f"질문 {len(questions)}개, Re-ranker {rag.RERANK_MODEL}, 1차 후보 검색기별 {rag.FETCH_K}개, "
          f"Top-{rag.TOP_K}")
    plans = make_plans(transformer, questions)
    report(evaluate(questions, plans, store, bm25, reranker, fuse))
    return 0


if __name__ == "__main__":
    sys.exit(main())
