"""BM25 : 벡터 가중치 실험.

정답 청크를 아는 질문 세트로 가중치별 Hit@K와 MRR@K를 비교함.
질문마다 BM25·벡터 검색을 한 번씩만 하고(Cohere 임베딩 호출 = 질문 수),
가중치를 바꿔 가며 EnsembleRetriever.weighted_reciprocal_rank()로 다시 합침.
LLM은 호출하지 않음.

실행:
    python evaluate_weights.py              # 기본 fetch-k(4)로 실험
    python evaluate_weights.py --fetch-k 10 # 검색기별 후보 수를 바꿔 실험
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time

from cohere.errors import TooManyRequestsError

import hybrid_rag as rag

# (질문 유형, 질문, 정답 청크 chunk_index) - 정답은 해당 조문 제목이 들어 있는 청크
# keyword: 법률 용어·조문 번호가 그대로 들어간 질문 / natural: 일상어로 바꿔 쓴 질문
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
ARTICLE_SAMPLE = 30  # article 유형: 무작위로 뽑은 조문 번호 질문 수
SEED = 42
RATE_LIMIT_WAIT_SEC = 60  # Cohere 트라이얼 키: 분당 100회 호출 제한
QTYPES = ("keyword", "natural", "article")
WEIGHTS = [round(w * 0.1, 1) for w in range(11)]  # BM25 가중치 0.0 ~ 1.0
_ARTICLE_HEAD = re.compile(r"(?:^|\n)(제\d+조(?:의\d+)?)\(")


def article_set(docs: list, n: int = ARTICLE_SAMPLE, seed: int = SEED) -> list[tuple[str, str, set[int]]]:
    """조문 제목이 있는 청크에서 조문 n개를 뽑아 "제N조의 내용은?" 질문을 만듦.

    사람이 고른 질문은 한쪽 검색기에 유리하게 치우칠 수 있어, 조문 번호 검색은
    고정 시드로 무작위 추출함.
    """
    heads: dict[str, set[int]] = {}
    for d in docs:
        for art in _ARTICLE_HEAD.findall(d.page_content):
            heads.setdefault(art, set()).add(d.metadata["chunk_index"])
    picked = random.Random(seed).sample(sorted(heads), min(n, len(heads)))
    return [("article", f"{art}의 내용은?", heads[art]) for art in picked]


def search(retriever, question: str, retries: int = 3) -> list:
    """Cohere 호출 한도(429)에 걸리면 1분 쉬고 다시 검색함."""
    for attempt in range(retries + 1):
        try:
            return retriever.invoke(question)
        except TooManyRequestsError:
            if attempt == retries:
                raise
            print(f"[rate-limit] Cohere 호출 한도 초과 → {RATE_LIMIT_WAIT_SEC}초 대기 후 재시도")
            time.sleep(RATE_LIMIT_WAIT_SEC)


def first_hit_rank(docs: list, gold: set[int], k: int) -> int | None:
    for rank, d in enumerate(docs[:k], 1):
        if d.metadata.get("chunk_index") in gold:
            return rank
    return None


def evaluate(bm25, vector, questions, k: int = rag.TOP_K) -> dict[float, dict[str, tuple[float, float]]]:
    """가중치 → 질문 유형(all/keyword/natural/article) → (Hit@k, MRR@k)."""
    cached = [(qtype, gold, bm25.invoke(q), search(vector, q)) for qtype, q, gold in questions]

    report = {}
    for w in WEIGHTS:
        ensemble = rag.build_ensemble(bm25, vector, w)
        ranks: dict[str, list[int | None]] = {"all": []}
        for qtype, gold, bm25_docs, vector_docs in cached:
            fused = ensemble.weighted_reciprocal_rank([bm25_docs, vector_docs])
            r = first_hit_rank(fused, gold, k)
            ranks["all"].append(r)
            ranks.setdefault(qtype, []).append(r)
        report[w] = {
            name: (sum(r is not None for r in rs) / len(rs),
                   sum(1 / r for r in rs if r) / len(rs))
            for name, rs in ranks.items()
        }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BM25 : 벡터 가중치 실험")
    parser.add_argument("--fetch-k", type=int, default=rag.FETCH_K,
                        help=f"검색기별 후보 수 (기본 {rag.FETCH_K})")
    args = parser.parse_args(argv)

    rag.load_env()
    store = rag.load_vectorstore()
    bm25 = rag.load_bm25_retriever(k=args.fetch_k)
    rag.check_same_corpus(store, bm25)
    vector = rag.build_vector_retriever(store, args.fetch_k)

    questions = EVAL_SET + article_set(bm25.docs)
    k = rag.TOP_K
    counts = ", ".join(f"{t} {sum(q[0] == t for q in questions)}" for t in QTYPES)
    print(f"질문 {len(questions)}개 ({counts}), 검색기별 후보 {args.fetch_k}개, "
          f"RRF c={rag.RRF_C}, Top-{k} 기준 Hit / MRR\n")
    header = f"{'BM25:벡터':<10}|" + "|".join(f"{name:^14}" for name in ("all",) + QTYPES)
    print(header)
    print("-" * len(header))
    report = evaluate(bm25, vector, questions, k)
    for w, m in report.items():
        cells = "|".join(f" {m[name][0]:>5.0%} {m[name][1]:>6.3f} " for name in ("all",) + QTYPES)
        note = {0.0: " ← 벡터만", 1.0: " ← BM25만"}.get(w, "")
        print(f"{w:.1f} : {1 - w:.1f} |{cells}{note}")

    best = max(report, key=lambda w: report[w]["all"])
    print(f"\n최고 성능 BM25 가중치: {best} (Hit@{k} {report[best]['all'][0]:.0%}, "
          f"MRR@{k} {report[best]['all'][1]:.3f}) / 현재 기본값: {rag.BM25_WEIGHT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
