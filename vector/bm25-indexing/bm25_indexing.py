"""특허법 BM25 인덱싱 파이프라인.

공용 벡터 DB(vectordb/) 청크 로드 → Kiwi 형태소 토큰화 → BM25 인덱스 생성 → bm25/ 저장

PDF를 다시 읽거나 임베딩하지 않고, vector/indexing에서 만든 ChromaDB 컬렉션의
청크(본문·메타데이터·ID)를 그대로 가져옴. 그래서 벡터 검색과 BM25 검색이
완전히 같은 청크를 대상으로 하고, Hybrid 검색에서 결과를 합칠 수 있음.

실행:
    python bm25_indexing.py            # bm25/ 인덱스를 새로 만듦
    python bm25_indexing.py --dry-run  # 저장 없이 토큰화 결과만 확인
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import warnings
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import chromadb
import kiwipiepy
from kiwipiepy import Kiwi
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi

# langchain-community는 2026-06 아카이브되어 import 시 DeprecationWarning을 냄.
# BM25Retriever는 대체 패키지가 없어 교재와 같이 그대로 사용함
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="langchain_community")
    warnings.filterwarnings("ignore", message=".*langchain-community.*", category=DeprecationWarning)
    from langchain_community.retrievers import BM25Retriever

# ---------------------------------------------------------------------------
# 경로 및 설정
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # rag/
VECTORDB_DIR = PROJECT_ROOT / "vectordb"
BM25_DIR = PROJECT_ROOT / "bm25"
CORPUS_FILE = "corpus.jsonl"
META_FILE = "meta.json"

# 인덱싱(vector/indexing/indexing.py)과 같아야 하는 값
COLLECTION_NAME = "patent_law"

FORMAT_VERSION = 1
BM25_PARAMS = {"k1": 1.5, "b": 0.75}  # rank_bm25 BM25Okapi 기본값
TOP_K = 4

# ---------------------------------------------------------------------------
# 토크나이저 (vector/hybrid-search/hybrid_rag.py와 반드시 같아야 함)
# ---------------------------------------------------------------------------
# 검색에 의미 있는 품사만 남김: 일반/고유명사, 수사, 숫자, 외국어, 한자, 어근, 동사·형용사 어간
# 조사(JK*)·어미(E*)·접사·기호는 버림. 동사/형용사의 규칙·불규칙 표시(VV-R 등)는 떼고 비교함
KEEP_TAGS = ("NNG", "NNP", "NR", "SN", "SL", "SH", "XR", "VV", "VA")
# Kiwi는 "제42조"를 제/42/조로 쪼개므로, 조문 번호는 정규식으로 한 토큰("제42조")을 더 만듦
ARTICLE_REF = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")


@lru_cache(maxsize=1)
def get_kiwi() -> Kiwi:
    """Kiwi 모델 로드는 1초 가량 걸리므로 한 번만 만듦."""
    return Kiwi()


def tokenize(text: str) -> list[str]:
    """BM25용 토큰 리스트. 인덱싱과 질의에 같은 함수를 써야 함."""
    tokens = [f"제{num}조" + (f"의{sub}" if sub else "")
              for num, sub in ARTICLE_REF.findall(text)]
    tokens += [t.form for t in get_kiwi().tokenize(text)
               if t.tag.split("-")[0] in KEEP_TAGS]
    return tokens


def tokenizer_config() -> dict:
    """meta.json에 기록해 검색 쪽 토크나이저와 일치하는지 확인하는 데 씀."""
    return {
        "library": "kiwipiepy",
        "version": kiwipiepy.__version__,
        "keep_tags": list(KEEP_TAGS),
        "article_token": ARTICLE_REF.pattern,
    }


# ---------------------------------------------------------------------------
# 1. 벡터 DB에서 청크 로드 (임베딩 없음)
# ---------------------------------------------------------------------------
def load_chunks(db_dir: Path | None = None) -> list[Document]:
    """공용 ChromaDB 컬렉션의 청크를 chunk_index 순서의 Document로 가져옴."""
    db_dir = db_dir or VECTORDB_DIR
    if not (db_dir / "chroma.sqlite3").exists():
        raise FileNotFoundError(
            f"벡터 DB가 없습니다: {db_dir}\n"
            "먼저 vector/indexing에서 python indexing.py를 실행하세요.")

    client = chromadb.PersistentClient(path=str(db_dir))
    if COLLECTION_NAME not in [c.name for c in client.list_collections()]:
        raise LookupError(f"'{COLLECTION_NAME}' 컬렉션이 없습니다: {db_dir}")

    result = client.get_collection(COLLECTION_NAME).get(include=["documents", "metadatas"])
    if not result["ids"]:
        raise ValueError(f"'{COLLECTION_NAME}' 컬렉션이 비어 있습니다.")

    docs = [
        Document(id=id_, page_content=text, metadata=meta)
        for id_, text, meta in zip(result["ids"], result["documents"], result["metadatas"])
    ]
    return sorted(docs, key=lambda d: (d.metadata.get("source", ""),
                                       d.metadata.get("chunk_index", 0)))


# ---------------------------------------------------------------------------
# 2. 토큰화 + BM25 인덱스
# ---------------------------------------------------------------------------
def build_retriever(docs: list[Document], tokens: list[list[str]],
                    k: int = TOP_K) -> BM25Retriever:
    """토큰화된 코퍼스로 LangChain BM25Retriever를 만듦."""
    if len(docs) != len(tokens):
        raise ValueError(f"문서 수({len(docs)})와 토큰 리스트 수({len(tokens)})가 다릅니다.")
    return BM25Retriever(
        vectorizer=BM25Okapi(tokens, **BM25_PARAMS),
        docs=docs,
        preprocess_func=tokenize,
        k=k,
    )


# ---------------------------------------------------------------------------
# 3. 저장 / 로드
# ---------------------------------------------------------------------------
def save_index(docs: list[Document], tokens: list[list[str]],
               out_dir: Path | None = None) -> Path:
    """corpus.jsonl(청크+토큰)과 meta.json을 저장함.

    pickle 대신 JSON으로 저장해 사람이 토큰화 결과를 열어 볼 수 있고,
    라이브러리 버전이 바뀌어도 읽을 수 있음. 임시 디렉토리에 다 쓴 뒤에
    교체하므로 중간에 실패해도 기존 인덱스는 그대로 남음.
    """
    out_dir = out_dir or BM25_DIR
    staging = out_dir.with_name(out_dir.name + "__staging")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    with open(staging / CORPUS_FILE, "w", encoding="utf-8") as f:
        for doc, toks in zip(docs, tokens, strict=True):
            record = {"id": doc.id, "page_content": doc.page_content,
                      "metadata": doc.metadata, "tokens": toks}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    meta = {
        "format_version": FORMAT_VERSION,
        "collection": COLLECTION_NAME,
        "doc_count": len(docs),
        "tokenizer": tokenizer_config(),
        "bm25_params": BM25_PARAMS,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (staging / META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    shutil.rmtree(out_dir, ignore_errors=True)
    staging.rename(out_dir)
    return out_dir


def load_index(index_dir: Path | None = None) -> tuple[list[Document], list[list[str]], dict]:
    """저장된 인덱스를 읽음. (docs, tokens, meta)"""
    index_dir = index_dir or BM25_DIR
    meta = json.loads((index_dir / META_FILE).read_text(encoding="utf-8"))
    docs, tokens = [], []
    with open(index_dir / CORPUS_FILE, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            docs.append(Document(id=r["id"], page_content=r["page_content"],
                                 metadata=r["metadata"]))
            tokens.append(r["tokens"])
    if len(docs) != meta["doc_count"]:
        raise ValueError(f"인덱스 손상: meta={meta['doc_count']}건, corpus={len(docs)}건")
    return docs, tokens, meta


# ---------------------------------------------------------------------------
# 4. 실행
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="특허법 BM25 인덱싱 (Kiwi 형태소 분석)")
    parser.add_argument("--dry-run", action="store_true", help="저장 없이 토큰화 결과만 출력")
    parser.add_argument("--out", type=Path, default=BM25_DIR, help=f"인덱스 디렉토리 (기본 {BM25_DIR})")
    args = parser.parse_args(argv)

    docs = load_chunks()
    print(f"[load] {VECTORDB_DIR} / '{COLLECTION_NAME}': {len(docs)}개 청크")

    tokens = [tokenize(d.page_content) for d in docs]
    lengths = [len(t) for t in tokens]
    vocab = {tok for toks in tokens for tok in toks}
    print(f"[tokenize] Kiwi {kiwipiepy.__version__}: 청크당 평균 {sum(lengths) / len(lengths):.0f}토큰, "
          f"어휘 {len(vocab):,}개")
    if empty := [d.id for d, t in zip(docs, tokens) if not t]:
        print(f"[warn] 토큰이 없는 청크 {len(empty)}개: {empty[:5]}")

    if args.dry_run:
        for doc, toks in list(zip(docs, tokens))[17:19]:
            print("-" * 60, doc.id, doc.page_content[:100].replace("\n", " "),
                  f"→ {toks[:30]}", sep="\n")
        return 0

    out_dir = save_index(docs, tokens, args.out)
    print(f"[save] {out_dir} ({CORPUS_FILE}, {META_FILE})")

    # 저장한 인덱스를 다시 읽어 검색이 되는지 확인
    loaded_docs, loaded_tokens, _ = load_index(out_dir)
    retriever = build_retriever(loaded_docs, loaded_tokens, k=3)
    query = "특허를 받을 수 있는 조건은?"
    print(f"\n[check] '{query}' → 토큰 {tokenize(query)}")
    for doc in retriever.invoke(query):
        preview = doc.page_content[:70].replace("\n", " ")
        print(f"  - {doc.id} | {preview}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
