"""indexing.py 테스트.

- 단위 테스트: 정제·청킹·메타데이터 (API 호출 없음)
- 통합 테스트: 구축된 vectordb 로드·검색 (COHERE_API_KEY와 vectordb 필요)

실행: pytest -v
"""

import gc
import os
import re
import sqlite3
from contextlib import closing

import chromadb
import pytest
from chromadb.api.client import SharedSystemClient
from cohere.errors import TooManyRequestsError
from dotenv import load_dotenv
from langchain_core.embeddings import DeterministicFakeEmbedding

import indexing as ix


@pytest.fixture(scope="module")
def pdf_pages():
    docs = ix.load_pdfs()
    assert "특허법.pdf" in docs
    return [page.page_content for page in docs["특허법.pdf"]]


@pytest.fixture(scope="module")
def cleaned(pdf_pages):
    return ix.clean_text(pdf_pages)


@pytest.fixture(scope="module")
def chunks():
    return ix.prepare_chunks()


# ---------------------------------------------------------------------------
# 정제
# ---------------------------------------------------------------------------
def test_page_header_removed(cleaned):
    assert "법제처" not in cleaned
    assert "국가법령정보센터" not in cleaned


def test_amendment_notes_removed(cleaned):
    assert not re.search(r"<\s*(개정|신설)", cleaned)
    assert not re.search(r"\[(전문개정|본조신설|제목개정|종전)", cleaned)


def test_body_brackets_kept(cleaned):
    # 수수료 관련 본문 대괄호는 개정 주석이 아니므로 남아야 함
    assert "[대리인이 특허법인" in cleaned


def test_wrapped_lines_joined(cleaned):
    assert "이바지함을 목적으로 한다." in cleaned       # 단어 사이 줄바꿈 → 공백
    assert "그 밖의 절차(이하 “특허에 관한 절차”" in cleaned  # 단어 중간 줄바꿈 → 붙임
    assert "특허 에 관한" not in cleaned


def test_articles_separated_by_blank_line(cleaned):
    blocks = cleaned.split("\n\n")
    assert any(b.startswith("제29조(특허요건)") for b in blocks)
    assert any(b.startswith("제1장 총칙") for b in blocks)


def test_clean_text_inline_sample():
    # "특허\n에" → 문서 어휘에 "특허에"가 있으므로 붙임, "번째\n항" → "번째항"이 없으므로 공백
    pages = [
        "법제처   3   국가법령정보센터\n특허법\n"
        "제10조(테스트) ① 특허\n에 관한 절차를 밟을 수 있다. <개정 2014. 6. 11.>\n"
        "② 특허에 대한 두 번째\n항이다.<개\n정 2025. 10. 1.>\n[전문개정 2014. 6. 11.]\n \n"
        "제11조 삭제 <2001. 2. 3.>",
    ]
    assert ix.clean_text(pages) == (
        "제10조(테스트) ① 특허에 관한 절차를 밟을 수 있다.\n"
        "② 특허에 대한 두 번째 항이다.\n\n"
        "제11조 삭제"
    )


# ---------------------------------------------------------------------------
# 청킹 + 메타데이터
# ---------------------------------------------------------------------------
def test_chunk_size_limit(chunks):
    assert chunks
    assert all(len(c.page_content) <= ix.CHUNK_SIZE for c in chunks)


def test_metadata_fields(chunks):
    total = len(chunks)
    for i, c in enumerate(chunks):
        assert set(c.metadata) == {"source", "chunk_index", "total_chunks", "char_count"}
        assert c.metadata["source"] == "특허법.pdf"
        assert c.metadata["chunk_index"] == i
        assert c.metadata["total_chunks"] == total
        assert c.metadata["char_count"] == len(c.page_content)


def test_chunks_start_at_legal_boundary(chunks):
    # 대부분의 청크가 조/항/호/목 또는 장/부칙 표지로 시작해야 함
    marker = re.compile(r"^(제\d+(조|장)|[①-⑳]|\d+\.\s|[가-하]\.\s|부칙|특허법)")
    ratio = sum(bool(marker.match(c.page_content)) for c in chunks) / len(chunks)
    assert ratio >= 0.9, f"법령 경계 시작 비율 {ratio:.2%}"


def test_splitter_prefers_paragraph_boundary():
    article = "제1조(테스트) " + "① " + "가" * 500 + "\n② " + "나" * 500
    pieces = ix.build_splitter().split_text(article)
    assert len(pieces) == 2
    assert pieces[1].startswith("② ")


# ---------------------------------------------------------------------------
# 저장: 가짜 임베딩 + 임시 디렉토리 (API 호출 없음)
# ---------------------------------------------------------------------------
class FlakyEmbeddings(DeterministicFakeEmbedding):
    """fail_on번째 embed_documents 호출에서 실패하는 가짜 임베딩."""

    fail_on: int = 0
    calls: int = 0

    def embed_documents(self, texts):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("simulated API failure")
        return super().embed_documents(texts)


@pytest.fixture
def offline_db(monkeypatch, tmp_path, chunks):
    emb = FlakyEmbeddings(size=8)
    monkeypatch.setattr(ix, "VECTORDB_DIR", tmp_path / "vectordb")
    monkeypatch.setattr(ix, "get_embeddings", lambda: emb)
    return emb


def test_build_vectorstore_offline(offline_db, chunks):
    store = ix.build_vectorstore(chunks)
    assert store._collection.count() == len(chunks)
    # 재실행해도 중복 없이 같은 개수 + 스테이징 컬렉션 잔여 없음
    store = ix.build_vectorstore(chunks)
    assert store._collection.count() == len(chunks)
    client = chromadb.PersistentClient(path=str(ix.VECTORDB_DIR))
    assert [c.name for c in client.list_collections()] == [ix.COLLECTION_NAME]


def test_orphan_segments_cleaned(offline_db, chunks):
    for _ in range(3):
        ix.build_vectorstore(chunks)
    # 실제 실행은 매번 새 프로세스이므로, 프로세스 종료처럼 Chroma 파일 핸들을 해제
    SharedSystemClient.clear_system_cache()
    gc.collect()
    ix.remove_orphan_segments()
    with closing(sqlite3.connect(ix.VECTORDB_DIR / "chroma.sqlite3")) as con:
        live = {row[0] for row in con.execute("SELECT id FROM segments")}
    dirs = {p.name for p in ix.VECTORDB_DIR.iterdir() if p.is_dir()}
    assert dirs and dirs <= live, f"orphans: {dirs - live}"


def test_failed_rebuild_keeps_existing_db(offline_db, chunks):
    ix.build_vectorstore(chunks)
    offline_db.calls, offline_db.fail_on = 0, 2  # 두 번째 배치에서 실패
    with pytest.raises(RuntimeError):
        ix.build_vectorstore(chunks)
    store = ix.get_vectorstore(offline_db)
    assert store._collection.count() == len(chunks)


def test_rate_limit_retry(monkeypatch):
    class Store:
        calls = 0

        def add_documents(self, docs, ids):
            Store.calls += 1
            if Store.calls < 3:
                raise TooManyRequestsError(body={"message": "rate limit"})

    monkeypatch.setattr(ix.time, "sleep", lambda _: None)
    ix.add_with_rate_limit(Store(), [], [])
    assert Store.calls == 3


# ---------------------------------------------------------------------------
# 통합: 구축된 벡터 DB
# ---------------------------------------------------------------------------
load_dotenv(ix.ENV_FILE)
needs_db = pytest.mark.skipif(
    not (os.getenv("COHERE_API_KEY") and ix.VECTORDB_DIR.exists()),
    reason="COHERE_API_KEY 또는 vectordb가 없음 (python indexing.py 먼저 실행)",
)


@pytest.fixture(scope="module")
def store():
    return ix.get_vectorstore()


@needs_db
def test_vectordb_count_matches_chunks(store, chunks):
    assert store._collection.count() == len(chunks)


@needs_db
def test_vectordb_metadata_and_dimension(store):
    got = store._collection.get(limit=1, include=["metadatas", "embeddings"])
    assert set(got["metadatas"][0]) == {"source", "chunk_index", "total_chunks", "char_count"}
    assert len(got["embeddings"][0]) == 1536  # embed-v4.0 기본 차원
    assert store._collection.metadata["embedding_model"] == ix.EMBEDDING_MODEL


@needs_db
@pytest.mark.parametrize(
    "query, expected",
    [
        ("특허를 받을 수 있는 조건은?", "제29조(특허요건)"),
        ("특허권의 존속기간은 몇 년인가?", "제88조(특허권의 존속기간)"),
        ("특허를 받을 수 없는 발명", "제32조(특허를 받을 수 없는 발명)"),
    ],
)
def test_similarity_search(store, query, expected):
    docs = store.similarity_search(query, k=4)
    assert any(expected in d.page_content for d in docs), [d.page_content[:40] for d in docs]
