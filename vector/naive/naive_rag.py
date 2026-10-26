"""특허법 Naive RAG 예제.

Query → Retriever(ChromaDB, Top-K) → Documents → LLM(Groq gpt-oss-120b) → Response

인덱싱은 하지 않고, vector/indexing에서 구축한 공용 벡터 DB(vectordb/)를 로드해 검색함.

실행:
    python naive_rag.py "특허를 받을 수 있는 조건은 ?"   # 질문 1회
    python naive_rag.py                                  # 대화형 (종료: exit)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_cohere import CohereEmbeddings
from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable, RunnablePassthrough
from langchain_groq import ChatGroq

# ---------------------------------------------------------------------------
# 경로 및 설정
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # rag/
VECTORDB_DIR = PROJECT_ROOT / "vectordb"
ENV_FILE = PROJECT_ROOT / ".env"

# 인덱싱(vector/indexing/indexing.py)과 반드시 같아야 하는 값
COLLECTION_NAME = "patent_law"
EMBEDDING_MODEL = "embed-v4.0"

LLM_MODEL = "openai/gpt-oss-120b"
TOP_K = 4
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
# 1. 환경변수
# ---------------------------------------------------------------------------
def load_env() -> None:
    """.env를 읽고 필요한 API 키가 있는지 확인함."""
    load_dotenv(ENV_FILE)
    missing = [k for k in ("COHERE_API_KEY", "GROQ_API_KEY") if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"{ENV_FILE}에 {', '.join(missing)}가 없습니다.")


# ---------------------------------------------------------------------------
# 2. 벡터 DB 로드 (인덱싱 없음)
# ---------------------------------------------------------------------------
def load_vectorstore(db_dir: Path | None = None) -> Chroma:
    """기존 ChromaDB 컬렉션을 엶.

    langchain Chroma는 컬렉션이 없으면 빈 컬렉션을 새로 만들기 때문에,
    먼저 chromadb 클라이언트로 존재 여부·임베딩 모델·건수를 확인함.
    """
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


# ---------------------------------------------------------------------------
# 3. Retriever + LLM + Chain
# ---------------------------------------------------------------------------
def build_retriever(store: Chroma, k: int = TOP_K) -> BaseRetriever:
    return store.as_retriever(search_type="similarity", search_kwargs={"k": k})


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


def build_chain(retriever: BaseRetriever, llm: BaseChatModel) -> Runnable:
    """question(str) → {"question", "docs", "answer"} 를 반환하는 LCEL 체인.

    근거 문서를 함께 돌려주기 위해 검색 결과(docs)를 상태에 남긴 뒤 답변을 생성함.
    """
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
# 4. 출력 / 실행
# ---------------------------------------------------------------------------
def print_result(result: dict) -> None:
    print(f"\n[답변]\n{result['answer'].strip()}\n")
    print(f"[근거 문서 Top {len(result['docs'])}]")
    for d in result["docs"]:
        preview = d.page_content[:70].replace("\n", " ")
        print(f"  - {d.metadata.get('source')} #{d.metadata.get('chunk_index')} | {preview}…")


def repl(chain: Runnable, input_fn=input) -> None:
    """대화형 모드. exit/quit/q/종료 또는 Ctrl+C·Ctrl+D로 끝냄."""
    print("특허법 Naive RAG 입니다. 질문을 입력하세요. (종료: exit)")
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
    parser = argparse.ArgumentParser(description="특허법 Naive RAG")
    parser.add_argument("question", nargs="*", help="질문 (생략하면 대화형 모드)")
    parser.add_argument("-k", type=int, default=TOP_K, help=f"검색할 청크 수 (기본 {TOP_K})")
    args = parser.parse_args(argv)

    load_env()
    chain = build_chain(build_retriever(load_vectorstore(), args.k), build_llm())

    if args.question:
        print_result(ask(chain, " ".join(args.question)))
    else:
        repl(chain)
    return 0


if __name__ == "__main__":
    sys.exit(main())
