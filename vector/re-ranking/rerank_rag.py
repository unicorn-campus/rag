"""특허법 Re-ranking RAG 예제.

Query ─┬─ BM25 검색(bm25/, Kiwi 형태소)  Top-N ─┐
       └─ 벡터 검색(vectordb/, Cohere)   Top-N ─┴─ EnsembleRetriever(합집합·중복 제거)
            → Cohere Re-ranker(rerank-multilingual-v3.0) → Top-K
            → LLM(Groq gpt-oss-120b) → Response

"Retrieve More, Rerank Better": 1차 검색(Bi-Encoder + BM25)으로 후보를 넓게 모으고,
Cross-Encoder 방식의 Re-ranker로 질문-청크 쌍을 직접 비교해 상위 K개만 LLM에 넘김.

--transform을 주면 검색 전에 질문 변환(Pre-Retrieval)을 함. LLM이 질문을 보고
rewrite / multi_query / hyde / step_back / decomposition / none 중 하나를 골라 질의를 만들고,
원 질문 + 변환 질의로 각각 1차 검색한 후보를 합쳐 Re-ranking함.
인덱싱은 하지 않음. vectordb/(vector/indexing)와 bm25/(vector/bm25-indexing)를 로드해서 사용함.

실행:
    python rerank_rag.py "특허를 받을 수 있는 조건은 ?"   # 질문 1회
    python rerank_rag.py                                  # 대화형 (종료: exit)
    python rerank_rag.py --compare "남의 특허를 허락 없이 쓰면 어떤 처벌을 받나요?"  # Re-ranking 전후 비교 (LLM 호출 없음)
    python rerank_rag.py --transform "논문으로 먼저 발표한 발명도 특허를 받을 수 있나요?"  # 질문 변환 사용
    python rerank_rag.py --transform --compare "논문으로 먼저 발표한 발명도 특허를 받을 수 있나요?"  # 변환 전후 비교
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
from typing import Literal

import chromadb
from dotenv import load_dotenv
from kiwipiepy import Kiwi
from langchain_chroma import Chroma
from langchain_classic.retrievers import ContextualCompressionRetriever, EnsembleRetriever
from langchain_cohere import CohereEmbeddings, CohereRerank
from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field
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

RERANK_MODEL = "rerank-multilingual-v3.0"
LLM_MODEL = "openai/gpt-oss-120b"
TOP_K = 4          # Re-ranking 후 LLM에 넘길 청크 수 (RERANK_K)
FETCH_K = 10       # 1차 검색에서 검색기별로 가져올 후보 수 (INITIAL_K, 합집합 최대 20개, evaluate_rerank.py 실험 결과)
BM25_WEIGHT = 0.3  # EnsembleRetriever 가중치 [BM25, 벡터] = [0.3, 0.7] (hybrid-search 실험 결과)
RRF_C = 60
MAX_TRANSFORMED = 4  # 질문 변환으로 만들 질의 최대 개수 (원 질문 제외)
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

# 질문 변환 프롬프트: 기법 선택 + 변환을 한 번의 LLM 호출로 수행 (구조화 출력 QueryPlan)
TRANSFORM_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "[목표]\n"
     "사용자 질문을 대한민국 특허법 조문 검색에 알맞은 검색 질의로 변환한다.\n\n"
     "[역할]\n"
     "당신은 특허법 조문 검색 질의를 설계하는 전문가다.\n\n"
     "[맥락]\n"
     "- 검색 대상은 특허법 조문 청크다. 조문은 \"제29조(특허요건) ① …한다.\"처럼 법률 용어로 쓰여 있다.\n"
     "- 검색은 BM25(키워드) + 벡터(의미) 검색 후 Re-ranker로 다시 정렬한다.\n"
     "- 원 질문은 항상 함께 검색되므로, 변환 질의는 원 질문이 놓칠 수 있는 조문을 찾는 데 집중한다.\n\n"
     "[처리]\n"
     "1. 질문이 조문 용어와 얼마나 다른지, 몇 개의 쟁점을 담고 있는지 분석한다.\n"
     "2. 아래 기법 중 가장 알맞은 하나를 고른다.\n"
     "   - none: 조문 번호나 법률 용어가 이미 들어 있는 명확한 질문. queries는 빈 리스트.\n"
     "   - rewrite: 구어체·일상어라 조문 용어와 다른 질문. 법률 용어로 바꾼 질의 1개.\n"
     "   - multi_query: 여러 표현으로 찾아야 하거나 관련 조문이 흩어져 있을 수 있는 질문. "
     "서로 다른 관점의 질의 3개.\n"
     "   - hyde: 질문과 조문의 형식 차이가 큰 질문. 그 질문에 답하는 조문처럼 쓴 가상 조문 1개(2~3문장).\n"
     "   - step_back: 구체적 사례를 묻는 질문이라 일반 원칙을 먼저 찾아야 하는 질문. 상위 개념 질의 1개.\n"
     "   - decomposition: 서로 다른 쟁점 둘 이상을 함께 묻는 복합 질문. 하위 질문 2~4개.\n"
     "3. 고른 기법의 규칙대로 queries를 만든다.\n\n"
     "[출력]\n"
     "- technique: 고른 기법 이름\n"
     "- reason: 고른 이유 (한국어 한 문장)\n"
     "- queries: 변환된 질의 목록 (none이면 빈 리스트)\n\n"
     "[제약조건]\n"
     "- 질의는 한국어로 쓰고, hyde를 뺀 질의는 각각 40자 이내로 쓴다.\n"
     "- 질문에 없는 사실을 지어내지 않는다. 조문 번호는 확실할 때만 쓴다.\n"
     "- 공지, 공연히 실시, 간행물, 실시권, 심판처럼 조문에 쓰이는 용어를 우선 쓴다.\n\n"
     "[예시]\n"
     "- \"제98조\" → none\n"
     "- \"회사에서 일하다 만든 발명은 누구 것인가요?\" → rewrite: "
     "\"직무발명에 대한 특허를 받을 수 있는 권리의 귀속\"\n"
     "- \"특허 출원할 때 내야 하는 서류는?\" → multi_query: "
     "\"특허출원서 기재 사항\", \"명세서와 도면 첨부\", \"요약서 제출\"\n"
     "- \"특허권이 공유일 때 마음대로 팔아도 되나요?\" → step_back: "
     "\"공유인 특허권의 지분 양도와 실시 제한\"\n"
     "- \"심사관이 거절하면 어떻게 다투고, 기간은 얼마나 되나요?\" → decomposition: "
     "\"특허거절결정에 대한 심판 청구\", \"거절결정 불복심판 청구 기간\""),
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
    """.env를 읽고 필요한 API 키가 있는지 확인함. (Cohere: 임베딩 + Re-ranking)"""
    load_dotenv(ENV_FILE)
    missing = [k for k in ("COHERE_API_KEY", "GROQ_API_KEY") if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"{ENV_FILE}에 {', '.join(missing)}가 없습니다.")


# ---------------------------------------------------------------------------
# 2. 인덱스 로드 (인덱싱 없음) - hybrid_rag.py와 같음
# ---------------------------------------------------------------------------
def load_vectorstore(db_dir: Path | None = None) -> Chroma:
    """기존 ChromaDB 컬렉션을 엶. 없거나 비었거나 임베딩 모델이 다르면 예외."""
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
    """bm25/의 토큰화된 코퍼스로 BM25Retriever를 다시 만듦 (토크나이저 설정 검증 포함)."""
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
    """두 인덱스가 같은 청크 집합인지 확인함 (EnsembleRetriever 중복 제거의 전제)."""
    vector_ids = set(store.get(include=[])["ids"])
    bm25_ids = {d.id for d in bm25.docs}
    if vector_ids != bm25_ids:
        raise ValueError(
            f"벡터 DB({len(vector_ids)}건)와 BM25 인덱스({len(bm25_ids)}건)의 청크가 다릅니다. "
            "bm25_indexing.py로 BM25 인덱스를 다시 만드세요.")


# ---------------------------------------------------------------------------
# 3. 1차 검색(Hybrid) + Re-ranking
# ---------------------------------------------------------------------------
def build_vector_retriever(store: Chroma, k: int = FETCH_K) -> BaseRetriever:
    return store.as_retriever(search_type="similarity", search_kwargs={"k": k})


def build_ensemble(bm25: BaseRetriever, vector: BaseRetriever,
                   bm25_weight: float = BM25_WEIGHT) -> EnsembleRetriever:
    """1차 검색기: BM25 + 벡터 결과의 합집합을 가중 RRF 순서로 돌려줌 (최대 2 × FETCH_K개)."""
    if not 0.0 <= bm25_weight <= 1.0:
        raise ValueError(f"BM25 가중치는 0~1 사이여야 합니다: {bm25_weight}")
    return EnsembleRetriever(retrievers=[bm25, vector],
                             weights=[bm25_weight, 1.0 - bm25_weight], c=RRF_C)


def build_reranker(top_n: int = TOP_K) -> CohereRerank:
    """Cohere Re-ranker. 질문과 청크를 함께 넣어 0~1 관련도 점수(relevance_score)를 매김."""
    return CohereRerank(model=RERANK_MODEL, top_n=top_n)


def build_rerank_retriever(first_stage: BaseRetriever, reranker: CohereRerank) -> ContextualCompressionRetriever:
    """1차 검색 결과 전체를 Re-ranker로 다시 정렬해 top_n개만 남기는 검색기.

    ContextualCompressionRetriever는 base_retriever로 문서를 가져온 뒤
    base_compressor(CohereRerank)의 compress_documents()로 걸러냄.
    """
    return ContextualCompressionRetriever(base_compressor=reranker, base_retriever=first_stage)


# ---------------------------------------------------------------------------
# 4. 질문 변환 (Pre-Retrieval, --transform)
# ---------------------------------------------------------------------------
Technique = Literal["none", "rewrite", "multi_query", "hyde", "step_back", "decomposition"]


class QueryPlan(BaseModel):
    """LLM이 고른 질문 변환 기법과 변환 결과."""

    technique: Technique = Field(description="선택한 질문 변환 기법")
    reason: str = Field(description="그 기법을 고른 이유 (한국어 한 문장)")
    queries: list[str] = Field(description="변환된 검색 질의 목록. none이면 빈 리스트")


def build_transformer(llm: BaseChatModel) -> Runnable:
    """question(str) → QueryPlan. 기법 선택과 변환을 LLM 한 번의 구조화 출력으로 처리함."""
    return (
        {"question": RunnablePassthrough()}
        | TRANSFORM_PROMPT
        | llm.with_structured_output(QueryPlan, method="json_schema")
    )


def search_queries(question: str, plan: QueryPlan) -> list[str]:
    """1차 검색에 쓸 질의 목록 (중복 제거).

    원 질문은 항상 맨 앞에 넣음. 변환이 빗나가도 원 질문의 후보는 그대로 남아,
    질문 변환은 후보를 '더할' 뿐 '잃게' 하지는 않음.
    """
    extra = [] if plan.technique == "none" else plan.queries[:MAX_TRANSFORMED]
    return list(dict.fromkeys([question, *(q.strip() for q in extra if q.strip())]))


def rerank_query(question: str, plan: QueryPlan) -> str:
    """Re-ranker에 넣을 질의: 원 질문 + 변환 질의를 줄바꿈으로 이은 문자열.

    변환 질의마다 따로 Re-ranking하면 Cohere 호출이 질의 수만큼 늘어나므로
    (트라이얼 키: 분당 10회, 월 1,000회) 한 번에 채점함. none이면 원 질문 그대로임.
    """
    return "\n".join(search_queries(question, plan))


def merge_candidates(doc_lists: list[list[Document]]) -> list[Document]:
    """질의별 1차 검색 결과를 합치고 page_content 기준으로 중복을 제거함 (먼저 나온 순서 유지)."""
    seen: set[str] = set()
    merged = []
    for docs in doc_lists:
        for d in docs:
            if d.page_content not in seen:
                seen.add(d.page_content)
                merged.append(d)
    return merged


def rerank_docs(reranker: CohereRerank, docs: list[Document], query: str, k: int = TOP_K) -> list[Document]:
    """CohereRerank.rerank()로 채점해 상위 k개를 relevance_score와 함께 돌려줌. 후보가 없으면 호출 안 함."""
    if not docs:
        return []
    return [
        Document(docs[r["index"]].page_content,
                 metadata={**docs[r["index"]].metadata, "relevance_score": r["relevance_score"]})
        for r in reranker.rerank(docs, query, top_n=k)
    ]


def transform_and_retrieve(question: str, transformer: Runnable, first_stage: BaseRetriever,
                           reranker: CohereRerank, k: int = TOP_K) -> dict:
    """질문 변환 → 질의별 1차 검색(병렬) → 후보 합치기 → Re-ranking.

    반환: {"plan": QueryPlan, "queries": 1차 검색 질의, "candidates": 후보 수, "docs": Top-K}
    """
    plan = transformer.invoke(question)
    queries = search_queries(question, plan)
    candidates = merge_candidates(first_stage.batch(queries))
    docs = rerank_docs(reranker, candidates, rerank_query(question, plan), k)
    return {"plan": plan, "queries": queries, "candidates": len(candidates), "docs": docs}


# ---------------------------------------------------------------------------
# 5. LLM + Chain
# ---------------------------------------------------------------------------
def build_llm(max_retries: int = 2) -> ChatGroq:
    # gpt-oss는 추론 모델: 추론 내용은 additional_kwargs["reasoning_content"]로
    # 분리되어 오고, content에는 최종 답변만 담김
    return ChatGroq(model=LLM_MODEL, temperature=0, max_retries=max_retries)


def format_docs(docs: list[Document]) -> str:
    """검색된 청크를 출처 표기와 함께 하나의 컨텍스트 문자열로 합침."""
    return "\n\n".join(
        f"[출처: {d.metadata.get('source')} #{d.metadata.get('chunk_index')}]\n{d.page_content}"
        for d in docs
    )


def build_generator(llm: BaseChatModel) -> Runnable:
    """{"question", "docs"} → 답변 문자열."""
    return (
        {"context": lambda x: format_docs(x["docs"]), "question": lambda x: x["question"]}
        | PROMPT
        | llm
        | StrOutputParser()
    )


def build_chain(retriever: Runnable, llm: BaseChatModel) -> Runnable:
    """question(str) → {"question", "docs", "answer"} 를 반환하는 LCEL 체인 (질문 변환 없음)."""
    return (
        {"question": RunnablePassthrough(), "docs": retriever}
        | RunnablePassthrough.assign(answer=build_generator(llm))
    )


def build_transform_chain(transformer: Runnable, first_stage: BaseRetriever, reranker: CohereRerank,
                          llm: BaseChatModel, k: int = TOP_K) -> Runnable:
    """question(str) → {"question", "plan", "docs", "answer"} 를 반환하는 LCEL 체인 (질문 변환 사용).

    변환 질의는 검색에만 쓰고, 답변은 사용자의 원 질문으로 생성함.
    """
    retrieve = RunnableLambda(
        lambda q: transform_and_retrieve(q, transformer, first_stage, reranker, k), name="transform_retrieve")
    return (
        {"question": RunnablePassthrough(), "retrieval": retrieve}
        | RunnablePassthrough.assign(plan=lambda x: x["retrieval"]["plan"],
                                     docs=lambda x: x["retrieval"]["docs"])
        | RunnablePassthrough.assign(answer=build_generator(llm))
    )


def ask(chain: Runnable, question: str) -> dict:
    question = question.strip()
    if not question:
        raise ValueError("질문이 비어 있습니다.")
    return chain.invoke(question)


# ---------------------------------------------------------------------------
# 6. 비교 모드 (답변 LLM 호출 없음)
# ---------------------------------------------------------------------------
def compare_rerank(question: str, first_stage: BaseRetriever, reranker: CohereRerank,
                   k: int = TOP_K) -> dict:
    """1차 검색(RRF 순서) Top-K와 Re-ranking Top-K를 함께 돌려줌.

    1차 검색은 한 번만 하고, 그 후보 전체를 Re-ranker에 넘김. 각 결과에는
    1차 검색 순위(first_rank)를 붙여 Re-ranker가 몇 위 청크를 끌어올렸는지 보여 줌.
    """
    candidates = first_stage.invoke(question)
    first_rank = {d.metadata.get("chunk_index"): i for i, d in enumerate(candidates, 1)}
    reranked = list(reranker.compress_documents(candidates, question))[:k]
    for d in reranked:
        d.metadata["first_rank"] = first_rank.get(d.metadata.get("chunk_index"))
    return {"candidates": len(candidates), "first_stage": candidates[:k], "reranked": reranked}


def print_plan(plan: QueryPlan) -> None:
    print(f"[질문 변환] {plan.technique} - {plan.reason}")
    for q in plan.queries:
        print(f"  · {q}")


def print_comparison(question: str, result: dict) -> None:
    print(f"\n[질문] {question}")
    print(f"[1차 검색 후보] {result['candidates']}개 (BM25 + 벡터 합집합)")
    print(f"\n[1차 검색 Top {len(result['first_stage'])}] (가중 RRF 순서)")
    for rank, d in enumerate(result["first_stage"], 1):
        preview = d.page_content[:55].replace("\n", " ")
        print(f"  {rank}. #{d.metadata.get('chunk_index'):<3} | {preview}…")
    print(f"\n[Re-ranking Top {len(result['reranked'])}] ({RERANK_MODEL})")
    for rank, d in enumerate(result["reranked"], 1):
        preview = d.page_content[:45].replace("\n", " ")
        print(f"  {rank}. #{d.metadata.get('chunk_index'):<3} score={d.metadata['relevance_score']:.3f} "
              f"(1차 {d.metadata['first_rank']}위) | {preview}…")

    if (transformed := result.get("transform")) is None:
        return
    before = {d.metadata.get("chunk_index") for d in result["reranked"]}
    print()
    print_plan(transformed["plan"])
    print(f"[변환 후 1차 검색 후보] {transformed['candidates']}개 (질의 {len(transformed['queries'])}개의 합집합)")
    print(f"\n[질문 변환 + Re-ranking Top {len(transformed['docs'])}]")
    for rank, d in enumerate(transformed["docs"], 1):
        preview = d.page_content[:45].replace("\n", " ")
        mark = "" if d.metadata.get("chunk_index") in before else " (새로 진입)"
        print(f"  {rank}. #{d.metadata.get('chunk_index'):<3} score={d.metadata['relevance_score']:.3f}"
              f"{mark} | {preview}…")


# ---------------------------------------------------------------------------
# 7. 출력 / 실행
# ---------------------------------------------------------------------------
def print_result(result: dict) -> None:
    if result.get("plan") is not None:
        print()
        print_plan(result["plan"])
    print(f"\n[답변]\n{result['answer'].strip()}\n")
    print(f"[근거 문서 Top {len(result['docs'])}] (Re-ranking 관련도 점수)")
    for d in result["docs"]:
        preview = d.page_content[:60].replace("\n", " ")
        score = d.metadata.get("relevance_score")
        score_text = f"score={score:.3f} " if score is not None else ""
        print(f"  - {d.metadata.get('source')} #{d.metadata.get('chunk_index')} {score_text}| {preview}…")


def repl(chain: Runnable, input_fn=input) -> None:
    """대화형 모드. exit/quit/q/종료 또는 Ctrl+C·Ctrl+D로 끝냄."""
    print("특허법 Re-ranking RAG 입니다. 질문을 입력하세요. (종료: exit)")
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
    parser = argparse.ArgumentParser(description="특허법 Re-ranking RAG (Hybrid 검색 + Cohere Rerank)")
    parser.add_argument("question", nargs="*", help="질문 (생략하면 대화형 모드)")
    parser.add_argument("-k", type=int, default=TOP_K, help=f"Re-ranking 후 LLM에 넘길 청크 수 (기본 {TOP_K})")
    parser.add_argument("--fetch-k", type=int, default=FETCH_K,
                        help=f"1차 검색에서 검색기별로 가져올 후보 수 (기본 {FETCH_K})")
    parser.add_argument("-w", "--bm25-weight", type=float, default=BM25_WEIGHT,
                        help=f"1차 검색 BM25 가중치 0~1 (기본 {BM25_WEIGHT})")
    parser.add_argument("--compare", action="store_true",
                        help="답변 생성 없이 검색 결과만 비교 (--transform과 함께 쓰면 변환 전후도 비교)")
    parser.add_argument("-t", "--transform", action="store_true",
                        help="질문 변환 사용: LLM이 rewrite/multi_query/hyde/step_back/decomposition/none 중 선택")
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
    first_stage = build_ensemble(bm25, build_vector_retriever(store, args.fetch_k), args.bm25_weight)
    reranker = build_reranker(args.k)
    llm = build_llm()
    transformer = build_transformer(llm) if args.transform else None

    if args.compare:
        question = " ".join(args.question)
        result = compare_rerank(question, first_stage, reranker, args.k)
        if transformer is not None:
            result["transform"] = transform_and_retrieve(question, transformer, first_stage, reranker, args.k)
        print_comparison(question, result)
        return 0

    if transformer is not None:
        chain = build_transform_chain(transformer, first_stage, reranker, llm, args.k)
    else:
        chain = build_chain(build_rerank_retriever(first_stage, reranker), llm)
    if args.question:
        print_result(ask(chain, " ".join(args.question)))
    else:
        repl(chain)
    return 0


if __name__ == "__main__":
    sys.exit(main())
