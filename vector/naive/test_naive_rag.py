"""naive_rag.py 테스트.

- 단위 테스트: 포맷·체인·대화형 모드·DB 검증 로직 (가짜 LLM/Retriever, API 호출 없음)
- 통합 테스트: 공용 vectordb 검색 + Groq LLM 답변 (COHERE_API_KEY, GROQ_API_KEY, vectordb 필요)

실행: pytest -v
"""

import os
import subprocess
import sys
from pathlib import Path

import chromadb
import pytest
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.retrievers import BaseRetriever

import naive_rag as rag

DOCS = [
    Document(page_content="제29조(특허요건) ① 산업상 이용할 수 있는 발명…",
             metadata={"source": "특허법.pdf", "chunk_index": 17}),
    Document(page_content="제32조(특허를 받을 수 없는 발명) 공공의 질서…",
             metadata={"source": "특허법.pdf", "chunk_index": 21}),
]


class FakeRetriever(BaseRetriever):
    docs: list[Document]
    queries: list[str] = []

    def _get_relevant_documents(self, query, *, run_manager=None):
        self.queries.append(query)
        return self.docs


class RecordingLLM(FakeListChatModel):
    """LLM에 전달된 프롬프트 메시지를 기록하는 가짜 모델."""

    prompts: list = []

    def _call(self, messages, *args, **kwargs):
        self.prompts.append(messages)
        return super()._call(messages, *args, **kwargs)


# ---------------------------------------------------------------------------
# 단위 테스트
# ---------------------------------------------------------------------------
def test_format_docs_includes_source_and_content():
    text = rag.format_docs(DOCS)
    assert "[출처: 특허법.pdf #17]\n제29조(특허요건)" in text
    assert "[출처: 특허법.pdf #21]\n제32조" in text


def test_chain_returns_answer_and_docs_and_injects_context():
    retriever = FakeRetriever(docs=DOCS)
    llm = RecordingLLM(responses=["제29조에 따라 산업상 이용 가능해야 합니다."])
    result = rag.ask(rag.build_chain(retriever, llm), "  특허 요건은?  ")

    assert result["question"] == "특허 요건은?"
    assert result["docs"] == DOCS
    assert result["answer"] == "제29조에 따라 산업상 이용 가능해야 합니다."
    assert retriever.queries == ["특허 요건은?"]  # 검색은 정확히 1회(Naive RAG)
    system, human = llm.prompts[-1]
    assert "제29조(특허요건)" in system.content and "#21" in system.content
    assert human.content == "특허 요건은?"


def test_ask_rejects_empty_question():
    chain = rag.build_chain(FakeRetriever(docs=DOCS), FakeListChatModel(responses=["x"]))
    with pytest.raises(ValueError):
        rag.ask(chain, "   ")


def test_repl_handles_blank_errors_and_exit(capsys):
    calls = []

    class Chain:
        def invoke(self, q):
            calls.append(q)
            if q == "에러":
                raise RuntimeError("boom")
            return {"question": q, "docs": DOCS, "answer": f"답:{q}"}

    inputs = iter(["", "특허란?", "에러", "exit", "실행되면 안 됨"])
    rag.repl(Chain(), input_fn=lambda _: next(inputs))
    out = capsys.readouterr().out
    assert calls == ["특허란?", "에러"]
    assert "답:특허란?" in out and "[오류] RuntimeError: boom" in out


def test_repl_ends_on_eof():
    def eof(_):
        raise EOFError

    rag.repl(None, input_fn=eof)  # 예외 없이 종료되어야 함


def test_load_vectorstore_missing_db(tmp_path):
    with pytest.raises(FileNotFoundError):
        rag.load_vectorstore(tmp_path)


def test_load_vectorstore_rejects_wrong_or_empty_collection(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path))
    client.create_collection("other")
    with pytest.raises(LookupError):  # 컬렉션 없음 → 빈 컬렉션을 만들지 않고 실패
        rag.load_vectorstore(tmp_path)
    assert rag.COLLECTION_NAME not in [c.name for c in client.list_collections()]

    col = client.create_collection(rag.COLLECTION_NAME, metadata={"embedding_model": "other-model"})
    with pytest.raises(ValueError, match="임베딩 모델 불일치"):
        rag.load_vectorstore(tmp_path)

    col.modify(metadata={"embedding_model": rag.EMBEDDING_MODEL})
    with pytest.raises(ValueError, match="비어 있습니다"):
        rag.load_vectorstore(tmp_path)


# ---------------------------------------------------------------------------
# 통합 테스트 (실제 vectordb + Cohere + Groq)
# ---------------------------------------------------------------------------
load_dotenv(rag.ENV_FILE)
needs_api = pytest.mark.skipif(
    not (os.getenv("COHERE_API_KEY") and os.getenv("GROQ_API_KEY")
         and (rag.VECTORDB_DIR / "chroma.sqlite3").exists()),
    reason="API 키 또는 vectordb가 없음",
)


@pytest.fixture(scope="module")
def store():
    rag.load_env()
    return rag.load_vectorstore()


@pytest.fixture(scope="module")
def chain(store):
    return rag.build_chain(rag.build_retriever(store), rag.build_llm())


@needs_api
def test_existing_db_loaded_without_reindexing(store):
    client = chromadb.PersistentClient(path=str(rag.VECTORDB_DIR))
    assert [c.name for c in client.list_collections()] == [rag.COLLECTION_NAME]
    assert store._collection.count() == 191


@needs_api
def test_retriever_top_k(store):
    docs = rag.build_retriever(store).invoke("특허를 받을 수 있는 조건은 ?")
    assert len(docs) == rag.TOP_K
    assert "제29조(특허요건)" in docs[0].page_content


@needs_api
def test_answer_for_textbook_query(chain):
    result = rag.ask(chain, "특허를 받을 수 있는 조건은 ?")
    answer = result["answer"]
    assert "제29조" in answer
    assert "산업상 이용" in answer
    assert "공지" in answer  # 법률 용어 원문 유지


@needs_api
def test_answer_for_term_query(chain):
    result = rag.ask(chain, "특허권의 존속기간은 몇 년인가요?")
    assert "20년" in result["answer"]
    assert "제88조" in result["answer"]


@needs_api
def test_out_of_scope_question(chain):
    result = rag.ask(chain, "김치찌개 맛있게 끓이는 법 알려줘")
    assert "찾을 수 없습니다" in result["answer"]


@needs_api
def test_cli_one_shot():
    proc = subprocess.run(
        [sys.executable, str(Path(rag.__file__)), "특허출원은 누가 할 수 있나요?"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "[답변]" in proc.stdout and "[근거 문서 Top 4]" in proc.stdout
