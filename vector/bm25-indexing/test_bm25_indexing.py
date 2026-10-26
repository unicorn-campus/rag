"""bm25_indexing.py 테스트.

- 단위 테스트: 토크나이저, 검색기 생성, 저장/로드 왕복, 예외 처리 (API 호출 없음)
- 통합 테스트: 공용 vectordb에서 청크 로드 → 인덱스 생성 → 검색 (API 키 불필요, vectordb 필요)

실행: pytest -v
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from langchain_core.documents import Document

import bm25_indexing as bm

DOCS = [
    Document(id="특허법.pdf::00017", page_content="제29조(특허요건) ① 산업상 이용할 수 있는 발명은 특허를 받을 수 있다.",
             metadata={"source": "특허법.pdf", "chunk_index": 17}),
    Document(id="특허법.pdf::00073", page_content="제88조(특허권의 존속기간) ① 특허권의 존속기간은 설정등록한 날부터 20년이 되는 날까지로 한다.",
             metadata={"source": "특허법.pdf", "chunk_index": 73}),
    Document(id="특허법.pdf::00109", page_content="제132조의2(특허취소신청) 누구든지 특허취소신청을 할 수 있다.",
             metadata={"source": "특허법.pdf", "chunk_index": 109}),
]
HAS_VECTORDB = (bm.VECTORDB_DIR / "chroma.sqlite3").exists()


# ---------------------------------------------------------------------------
# 단위 테스트: 토크나이저
# ---------------------------------------------------------------------------
def test_tokenize_keeps_content_words_and_drops_particles():
    tokens = bm.tokenize("특허를 받을 수 있는 조건은?")
    assert {"특허", "받", "조건"} <= set(tokens)
    assert not {"를", "은", "?", "수"} & set(tokens)  # 조사·기호·의존명사 제거


def test_tokenize_adds_article_tokens():
    tokens = bm.tokenize("제 42 조 및 제132조의2에 따른 출원")
    assert tokens[:2] == ["제42조", "제132조의2"]
    assert "출원" in tokens


def test_tokenize_strips_irregular_suffix_from_verb_tags():
    # "받"은 Kiwi에서 VV-R 태그 → 접미 표시를 떼고 VV로 비교해야 남음
    assert "받" in bm.tokenize("받다")


def test_tokenize_empty():
    assert bm.tokenize("") == []


def test_tokenizer_config_is_serializable():
    cfg = bm.tokenizer_config()
    assert cfg["keep_tags"] == list(bm.KEEP_TAGS)
    json.dumps(cfg)


# ---------------------------------------------------------------------------
# 단위 테스트: 검색기 / 저장 / 로드
# ---------------------------------------------------------------------------
def test_build_retriever_ranks_keyword_match_first():
    tokens = [bm.tokenize(d.page_content) for d in DOCS]
    retriever = bm.build_retriever(DOCS, tokens, k=2)
    result = retriever.invoke("특허권 존속기간")
    assert len(result) == 2
    assert result[0].metadata["chunk_index"] == 73
    assert retriever.invoke("제132조의2")[0].metadata["chunk_index"] == 109


def test_build_retriever_rejects_length_mismatch():
    with pytest.raises(ValueError):
        bm.build_retriever(DOCS, [["a"]])


def test_save_and_load_roundtrip(tmp_path):
    tokens = [bm.tokenize(d.page_content) for d in DOCS]
    out = bm.save_index(DOCS, tokens, tmp_path / "bm25")

    assert sorted(p.name for p in out.iterdir()) == ["corpus.jsonl", "meta.json"]
    assert not (tmp_path / "bm25__staging").exists()
    docs, loaded_tokens, meta = bm.load_index(out)
    assert [d.id for d in docs] == [d.id for d in DOCS]
    assert docs[1].page_content == DOCS[1].page_content
    assert docs[1].metadata == DOCS[1].metadata
    assert loaded_tokens == tokens
    assert meta["doc_count"] == 3 and meta["tokenizer"] == bm.tokenizer_config()
    assert meta["bm25_params"] == bm.BM25_PARAMS


def test_save_overwrites_existing_index(tmp_path):
    out = tmp_path / "bm25"
    bm.save_index(DOCS, [bm.tokenize(d.page_content) for d in DOCS], out)
    bm.save_index(DOCS[:1], [bm.tokenize(DOCS[0].page_content)], out)
    docs, _, meta = bm.load_index(out)
    assert len(docs) == 1 and meta["doc_count"] == 1


def test_load_index_detects_corruption(tmp_path):
    out = bm.save_index(DOCS, [bm.tokenize(d.page_content) for d in DOCS], tmp_path / "bm25")
    lines = (out / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
    (out / "corpus.jsonl").write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="손상"):
        bm.load_index(out)


def test_load_chunks_missing_db(tmp_path):
    with pytest.raises(FileNotFoundError):
        bm.load_chunks(tmp_path)


# ---------------------------------------------------------------------------
# 통합 테스트: 실제 vectordb
# ---------------------------------------------------------------------------
needs_db = pytest.mark.skipif(not HAS_VECTORDB, reason="vectordb/가 없습니다")


@needs_db
def test_load_chunks_from_vectordb():
    docs = bm.load_chunks()
    assert len(docs) > 100
    indexes = [d.metadata["chunk_index"] for d in docs]
    assert indexes == sorted(indexes)  # chunk_index 순서
    assert docs[17].id == "특허법.pdf::00017"
    assert docs[17].page_content.startswith("제29조(특허요건)")


@needs_db
def test_end_to_end_index_and_search(tmp_path):
    docs = bm.load_chunks()
    tokens = [bm.tokenize(d.page_content) for d in docs]
    assert all(tokens)  # 토큰이 하나도 없는 청크가 없어야 함
    out = bm.save_index(docs, tokens, tmp_path / "bm25")

    retriever = bm.build_retriever(*bm.load_index(out)[:2], k=4)
    hits = [d.metadata["chunk_index"] for d in retriever.invoke("특허권의 존속기간")]
    assert 73 in hits  # 제88조(특허권의 존속기간)
    hits = [d.metadata["chunk_index"] for d in retriever.invoke("정정심판")]
    assert 124 in hits  # 제136조(정정심판)


@needs_db
def test_cli_dry_run_does_not_write(tmp_path):
    out = tmp_path / "bm25"
    proc = subprocess.run(
        [sys.executable, "bm25_indexing.py", "--dry-run", "--out", str(out)],
        cwd=Path(__file__).parent, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "[tokenize]" in proc.stdout and "제29조" in proc.stdout
    assert not out.exists()
