"""특허법 Hybrid Search RAG 예제.

Query ─┬─ BM25 검색(bm25/, Kiwi 형태소)   ─┐
       └─ 벡터 검색(vectordb/, Cohere)    ─┴─ EnsembleRetriever(가중 RRF) → Top-K
                                              → LLM(Groq gpt-oss-120b) → Response

인덱싱은 하지 않음. 벡터 검색은 vector/indexing이 만든 vectordb/를,
BM25 검색은 vector/bm25-indexing이 만든 bm25/를 로드해서 사용함.

실행:
    python hybrid_rag.py "특허를 받을 수 있는 조건은 ?"   # 질문 1회
    python hybrid_rag.py                                  # 대화형 (종료: exit)
    python hybrid_rag.py --compare "특허법 제42조"         # 검색기별 결과 비교 (LLM 호출 없음)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import warnings
from functools import lru_cache
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from kiwipiepy import Kiwi
from langchain_chroma import Chroma
from langchain_classic.retrievers import EnsembleRetriever
from langchain_cohere import CohereEmbeddings
from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough
from langchain_groq import ChatGroq
from rank_bm25 import BM25Okapi

# langchain-community는 2026-06 아카이브되어 import 시 DeprecationWarning을 냄.
# BM25Retriever는 대체 패키지가 없어 교재와 같이 그대로 사용함
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=".*langchain-community.*", category=DeprecationWarning)
    from langchain_community.retrievers import BM25Retriever

# ---------------------------------------------------------------------------
# 경로 및 설정
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # rag/
VECTORDB_DIR = PROJECT_ROOT / "vectordb"
BM25_DIR = PROJECT_ROOT / "bm25"
ENV_FILE = PROJECT_ROOT / ".env"

# 인덱싱(vector/indexing, vector/bm25-indexing)과 반드시 같아야 하는 값
COLLECTION_NAME = "patent_law"
EMBEDDING_MODEL = "embed-v4.0"
BM25_FORMAT_VERSION = 1

LLM_MODEL = "openai/gpt-oss-120b"
TOP_K = 4          # LLM에 넘길 최종 청크 수
FETCH_K = 4        # 검색기별로 RRF에 넣을 후보 수 (10으로 늘리면 BM25 하위 노이즈가 섞여 성능 하락)
BM25_WEIGHT = 0.3  # 가중치 [BM25, 벡터] = [0.3, 0.7] (evaluate_weights.py 실험 결과)
RRF_C = 60         # RRF 상수: score = Σ weight / (rank + c)
EXIT_COMMANDS = {"exit", "quit", "q", "종료"}

PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "당신은 대한민국 특허법 전문 상담가입니다.\n"
     "아래 <context>에 있는 특허법 조문만 근거로 질문에 한국어로 답하세요.\n"
     "- 답변에 근거 조문 번호(예: 제29조제1항)를 밝히세요.\n"
     "- 공지(公知), 공연(公然)히 같은 법률 용어는 바꾸지 말고 조문 원문 그대로 쓰세요.\n"
     "- <context>에 답이 없으면 추측하지 말고 "
     "\"제공된 특허법 문서에서 관련 내용을 찾을 수 없습니다.\"라고만 답하세요.\n\n"
     "<context>\n{context}\n</context>"),
    ("human", "{question}"),
])

# ---------------------------------------------------------------------------
# 토크나이저 (vector/bm25-indexing/bm25_indexing.py와 반드시 같아야 함)
# ---------------------------------------------------------------------------
KEEP_TAGS = ("NNG", "NNP", "NR", "SN", "SL", "SH", "XR", "VV", "VA")
ARTICLE_REF = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")


@lru_cache(maxsize=1)
def get_kiwi() -> Kiwi:
    return Kiwi()


def tokenize(text: str) -> list[str]:
    """질의를 인덱싱 때와 같은 방식으로 토큰화함 (조문 번호 토큰 + 내용어 형태소)."""
    tokens = [f"제{num}조" + (f"의{sub}" if sub else "")
              for num, sub in ARTICLE_REF.findall(text)]
    tokens += [t.form for t in get_kiwi().tokenize(text)
               if t.tag.split("-")[0] in KEEP_TAGS]
    return tokens


# ---------------------------------------------------------------------------
# 1. 환경변수
# ---------------------------------------------------------------------------
def load_env() -> None:
    """.env를 읽고 필요한 API 키가 있는지 확인함."""
    load_dotenv(ENV_FILE)
    missing = [k for k in ("COHERE_API_KEY", "GROQ_API_KEY") if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"{ENV_FILE}에 {', '.join(missing)}가 없습니다.")


# ---------------------------------------------------------------------------
# 2. 인덱스 로드 (인덱싱 없음)
# ---------------------------------------------------------------------------
def load_vectorstore(db_dir: Path | None = None) -> Chroma:
    """기존 ChromaDB 컬렉션을 엶. (naive_rag.py와 같은 검증 절차)"""
    db_dir = db_dir or VECTORDB_DIR
    if not (db_dir / "chroma.sqlite3").exists():
        raise FileNotFoundError(
            f"벡터 DB가 없습니다: {db_dir}\n"
            "먼저 vector/indexing에서 python indexing.py를 실행하세요.")

    client = chromadb.PersistentClient(path=str(db_dir))
    if COLLECTION_NAME not in [c.name for c in client.list_collections()]:
        raise LookupError(f"'{COLLECTION_NAME}' 컬렉션이 없습니다: {db_dir}")

    collection = client.get_collection(COLLECTION_NAME)
    indexed_model = (collection.metadata or {}).get("embedding_model")
    if indexed_model != EMBEDDING_MODEL:
        raise ValueError(f"임베딩 모델 불일치: DB={indexed_model}, 검색={EMBEDDING_MODEL}")
    if collection.count() == 0:
        raise ValueError(f"'{COLLECTION_NAME}' 컬렉션이 비어 있습니다.")

    return Chroma(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding_function=CohereEmbeddings(model=EMBEDDING_MODEL),
    )


def load_bm25_retriever(index_dir: Path | None = None, k: int = FETCH_K) -> BM25Retriever:
    """bm25/의 토큰화된 코퍼스로 BM25Retriever를 다시 만듦.

    토큰은 인덱싱 때 저장한 것을 그대로 쓰고 질의만 tokenize()로 토큰화하므로,
    두 프로그램의 토크나이저 설정이 같은지 meta.json으로 확인함.
    """
    index_dir = index_dir or BM25_DIR
    meta_path = index_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(
            f"BM25 인덱스가 없습니다: {index_dir}\n"
            "먼저 vector/bm25-indexing에서 python bm25_indexing.py를 실행하세요.")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("format_version") != BM25_FORMAT_VERSION:
        raise ValueError(f"BM25 인덱스 형식 불일치: {meta.get('format_version')}")
    if meta.get("collection") != COLLECTION_NAME:
        raise ValueError(f"BM25 인덱스 컬렉션 불일치: {meta.get('collection')}")
    tok = meta.get("tokenizer", {})
    if tuple(tok.get("keep_tags", ())) != KEEP_TAGS or tok.get("article_token") != ARTICLE_REF.pattern:
        raise ValueError("BM25 인덱스의 토크나이저 설정이 검색기와 다릅니다. "
                         "bm25_indexing.py로 인덱스를 다시 만드세요.")

    docs, tokens = [], []
    with open(index_dir / "corpus.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            docs.append(Document(id=r["id"], page_content=r["page_content"], metadata=r["metadata"]))
            tokens.append(r["tokens"])
    if len(docs) != meta["doc_count"] or not docs:
        raise ValueError(f"BM25 인덱스 손상: meta={meta['doc_count']}건, corpus={len(docs)}건")

    return BM25Retriever(
        vectorizer=BM25Okapi(tokens, **meta.get("bm25_params", {})),
        docs=docs,
        preprocess_func=tokenize,
        k=k,
    )


def check_same_corpus(store: Chroma, bm25: BM25Retriever) -> None:
    """두 인덱스가 같은 청크 집합인지 확인함.

    RRF는 같은 청크를 page_content로 묶어 점수를 더하므로, 벡터 DB만 다시 인덱싱하고
    BM25를 갱신하지 않으면 결합이 조용히 틀어짐. 이를 미리 막음.
    """
    vector_ids = set(store.get(include=[])["ids"])
    bm25_ids = {d.id for d in bm25.docs}
    if vector_ids != bm25_ids:
        raise ValueError(
            f"벡터 DB({len(vector_ids)}건)와 BM25 인덱스({len(bm25_ids)}건)의 청크가 다릅니다. "
            "bm25_indexing.py로 BM25 인덱스를 다시 만드세요.")


# ---------------------------------------------------------------------------
# 3. Hybrid Retriever + LLM + Chain
# ---------------------------------------------------------------------------
def build_vector_retriever(store: Chroma, k: int = FETCH_K) -> BaseRetriever:
    return store.as_retriever(search_type="similarity", search_kwargs={"k": k})


def build_ensemble(bm25: BaseRetriever, vector: BaseRetriever,
                   bm25_weight: float = BM25_WEIGHT) -> EnsembleRetriever:
    """BM25 + 벡터 검색 결과를 가중 RRF로 합치는 EnsembleRetriever."""
    if not 0.0 <= bm25_weight <= 1.0:
        raise ValueError(f"BM25 가중치는 0~1 사이여야 합니다: {bm25_weight}")
    return EnsembleRetriever(
        retrievers=[bm25, vector],
        weights=[bm25_weight, 1.0 - bm25_weight],
        c=RRF_C,
    )


def build_hybrid_retriever(bm25: BaseRetriever, vector: BaseRetriever,
                           bm25_weight: float = BM25_WEIGHT, k: int = TOP_K) -> Runnable:
    """EnsembleRetriever 결과에서 상위 k개만 남기는 Hybrid 검색기.

    EnsembleRetriever는 두 검색기 결과의 합집합(최대 2 × fetch_k개)을 RRF 점수순으로
    모두 돌려주므로, LLM 컨텍스트 크기를 Naive RAG와 같게 맞추려고 Top-K로 자름.
    (검색기별 후보 수 fetch_k는 각 검색기를 만들 때 정함)
    """
    return build_ensemble(bm25, vector, bm25_weight) | RunnableLambda(
        lambda docs: docs[:k], name=f"top_{k}")


def build_llm() -> ChatGroq:
    # gpt-oss는 추론 모델: 추론 내용은 additional_kwargs["reasoning_content"]로
    # 분리되어 오고, content에는 최종 답변만 담김
    return ChatGroq(model=LLM_MODEL, temperature=0, max_retries=2)


def format_docs(docs: list[Document]) -> str:
    """검색된 청크를 출처 표기와 함께 하나의 컨텍스트 문자열로 합침."""
    return "\n\n".join(
        f"[출처: {d.metadata.get('source')} #{d.metadata.get('chunk_index')}]\n{d.page_content}"
        for d in docs
    )


def build_chain(retriever: Runnable, llm: BaseChatModel) -> Runnable:
    """question(str) → {"question", "docs", "answer"} 를 반환하는 LCEL 체인."""
    generate = (
        {"context": lambda x: format_docs(x["docs"]), "question": lambda x: x["question"]}
        | PROMPT
        | llm
        | StrOutputParser()
    )
    return (
        {"question": RunnablePassthrough(), "docs": retriever}
        | RunnablePassthrough.assign(answer=generate)
    )


def ask(chain: Runnable, question: str) -> dict:
    question = question.strip()
    if not question:
        raise ValueError("질문이 비어 있습니다.")
    return chain.invoke(question)


# ---------------------------------------------------------------------------
# 4. 검색기 비교 (LLM 호출 없음)
# ---------------------------------------------------------------------------
def compare_retrievers(question: str, bm25: BaseRetriever, vector: BaseRetriever,
                       bm25_weight: float = BM25_WEIGHT, k: int = TOP_K) -> dict[str, list[Document]]:
    """같은 질문에 대한 BM25 / 벡터 / Hybrid 결과를 함께 돌려줌.

    검색은 검색기별 1회만 하고, Hybrid는 그 결과를 EnsembleRetriever의
    weighted_reciprocal_rank()로 합쳐 API 호출을 아낌.
    """
    bm25_docs, vector_docs = bm25.invoke(question), vector.invoke(question)
    fused = build_ensemble(bm25, vector, bm25_weight).weighted_reciprocal_rank(
        [bm25_docs, vector_docs])
    return {"bm25": bm25_docs[:k], "vector": vector_docs[:k], "hybrid": fused[:k]}


def print_comparison(question: str, results: dict[str, list[Document]], bm25_weight: float) -> None:
    print(f"\n[질문] {question}")
    print(f"[BM25 토큰] {tokenize(question)}")
    labels = {"bm25": "BM25", "vector": "벡터",
              "hybrid": f"Hybrid (BM25 {bm25_weight:.1f} : 벡터 {1 - bm25_weight:.1f})"}
    for key, label in labels.items():
        print(f"\n[{label} Top {len(results[key])}]")
        for rank, d in enumerate(results[key], 1):
            preview = d.page_content[:60].replace("\n", " ")
            print(f"  {rank}. #{d.metadata.get('chunk_index'):<3} | {preview}…")


# ---------------------------------------------------------------------------
# 5. 출력 / 실행
# ---------------------------------------------------------------------------
def print_result(result: dict) -> None:
    print(f"\n[답변]\n{result['answer'].strip()}\n")
    print(f"[근거 문서 Top {len(result['docs'])}]")
    for d in result["docs"]:
        preview = d.page_content[:70].replace("\n", " ")
        print(f"  - {d.metadata.get('source')} #{d.metadata.get('chunk_index')} | {preview}…")


def repl(chain: Runnable, input_fn=input) -> None:
    """대화형 모드. exit/quit/q/종료 또는 Ctrl+C·Ctrl+D로 끝냄."""
    print("특허법 Hybrid Search RAG 입니다. 질문을 입력하세요. (종료: exit)")
    while True:
        try:
            question = input_fn("\n질문> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question.lower() in EXIT_COMMANDS:
            break
        if not question:
            continue
        try:
            print_result(ask(chain, question))
        except Exception as e:  # 한 번의 실패로 대화가 끊기지 않도록 함
            print(f"[오류] {type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="특허법 Hybrid Search RAG (BM25 + 벡터)")
    parser.add_argument("question", nargs="*", help="질문 (생략하면 대화형 모드)")
    parser.add_argument("-k", type=int, default=TOP_K, help=f"LLM에 넘길 청크 수 (기본 {TOP_K})")
    parser.add_argument("--fetch-k", type=int, default=FETCH_K,
                        help=f"검색기별 후보 수 (기본 {FETCH_K})")
    parser.add_argument("-w", "--bm25-weight", type=float, default=BM25_WEIGHT,
                        help=f"BM25 가중치 0~1, 벡터 가중치는 1-w (기본 {BM25_WEIGHT})")
    parser.add_argument("--compare", action="store_true",
                        help="LLM 없이 BM25/벡터/Hybrid 검색 결과만 비교")
    args = parser.parse_args(argv)
    if args.k < 1 or args.fetch_k < args.k:
        parser.error("1 <= k <= fetch-k 이어야 합니다.")
    if not 0.0 <= args.bm25_weight <= 1.0:
        parser.error("--bm25-weight는 0~1 사이여야 합니다.")
    if args.compare and not args.question:
        parser.error("--compare에는 질문이 필요합니다.")

    load_env()
    store = load_vectorstore()
    bm25 = load_bm25_retriever(k=args.fetch_k)
    check_same_corpus(store, bm25)
    vector = build_vector_retriever(store, args.fetch_k)

    if args.compare:
        question = " ".join(args.question)
        print_comparison(question, compare_retrievers(question, bm25, vector, args.bm25_weight, args.k),
                         args.bm25_weight)
        return 0

    chain = build_chain(build_hybrid_retriever(bm25, vector, args.bm25_weight, args.k), build_llm())
    if args.question:
        print_result(ask(chain, " ".join(args.question)))
    else:
        repl(chain)
    return 0


if __name__ == "__main__":
    sys.exit(main())
