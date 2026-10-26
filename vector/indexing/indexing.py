"""특허법 PDF 인덱싱 파이프라인.

PDF 로드 → 정제 → 청킹 → 임베딩(Cohere embed-v4.0) → ChromaDB 저장

실행:
    python indexing.py            # 기존 컬렉션을 지우고 새로 구축
    python indexing.py --dry-run  # 임베딩/저장 없이 정제·청킹 결과만 확인
"""

from __future__ import annotations

import argparse
import gc
import os
import re
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import chromadb
from chromadb.api.client import SharedSystemClient
from cohere.errors import TooManyRequestsError
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_cohere import CohereEmbeddings
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ---------------------------------------------------------------------------
# 경로 및 설정
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # rag/
DATA_DIR = PROJECT_ROOT / "data"
VECTORDB_DIR = PROJECT_ROOT / "vectordb"
ENV_FILE = PROJECT_ROOT / ".env"

COLLECTION_NAME = "patent_law"
EMBEDDING_MODEL = "embed-v4.0"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 200
EMBED_BATCH_SIZE = 96  # Cohere Embed API의 요청당 최대 텍스트 수
RATE_LIMIT_WAIT_SEC = 60  # 트라이얼 키 한도: 분당 100,000 토큰
RATE_LIMIT_RETRIES = 4

# 법령 계층 구조(조 → 항 → 호 → 목 → 줄 → 문장 → 공백) 순서의 구분자.
# lookahead/lookbehind를 써서 ①, 1., 가. 같은 표지가 다음 청크 맨 앞에 남도록 함
SEPARATORS = [
    r"\n\n",               # 조문(article) 경계 - 정제 단계에서 \n\n으로 맞춤
    r"\n(?=[①-⑳])",        # 항(paragraph)
    r"\n(?=\d+\.\s)",      # 호(subparagraph)
    r"\n(?=[가-하]\.\s)",  # 목(item)
    r"\n",                 # 줄
    r"(?<=다\.)\s",        # 문장 종결("~한다. ")
    r" ",                  # 공백
    r"",                   # 문자 단위(최후 수단)
]

# ---------------------------------------------------------------------------
# 정제(clean)용 정규식
# ---------------------------------------------------------------------------
# 페이지 머리글: "법제처   12   국가법령정보센터\n특허법\n"
_PAGE_HEADER = re.compile(r"^\s*법제처\s+\d+\s+국가법령정보센터[^\n]*\n[^\n]*\n?")


def _spaced(word: str) -> str:
    """PDF 줄바꿈으로 단어가 끊겨도 매칭되도록 글자 사이에 \\s*를 넣음."""
    return r"\s*".join(map(re.escape, word))


# <개정 2014. 6. 11.>, <신설 2016. 2. 29.>, <2001. 2. 3.>
_ANGLE_NOTE = re.compile(rf"<\s*(?:{_spaced('개정')}|{_spaced('신설')})?[\s\d.,]*>")
# [전문개정 …], [본조신설 …], [제목개정 …], [종전 …], [제X조의Y에서 이동 …]
_BRACKET_NOTE = re.compile(
    r"\[\s*(?:"
    + "|".join(_spaced(w) for w in ("전문개정", "본조신설", "제목개정", "종전"))
    + r"|제\s*\d+\s*조(?:\s*의\s*\d+)?\s*에\s*서\s*이\s*동"
    + r")[^\[\]]*\]"
)
# 새 블록(\n\n)으로 시작해야 하는 줄: 장/절/관 제목, 조문 제목, 부칙
_BLOCK_START = re.compile(
    r"^(?:제\d+(?:장|절|관)\s|제\d+조(?:의\d+)?(?:\(|\s*삭제)|부\s*칙)"
)
# 줄바꿈을 유지해야 하는 줄: 항(①), 호(1.), 목(가.)
_LINE_START = re.compile(r"^(?:[①-⑳]|\d+\.\s|[가-하]\.\s)")
# Chroma 세그먼트 디렉토리 이름(UUID)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


# ---------------------------------------------------------------------------
# 1. 로드
# ---------------------------------------------------------------------------
def load_pdfs(data_dir: Path = DATA_DIR) -> dict[str, list[Document]]:
    """data 디렉토리의 PDF를 파일별 페이지 Document 리스트로 로드함."""
    pdf_paths = sorted(data_dir.glob("*.pdf"))
    if not pdf_paths:
        raise FileNotFoundError(f"PDF 파일이 없습니다: {data_dir}")
    return {path.name: PyPDFLoader(str(path)).load() for path in pdf_paths}


# ---------------------------------------------------------------------------
# 2. 정제
# ---------------------------------------------------------------------------
def clean_text(pages: list[str]) -> str:
    """페이지 텍스트 리스트를 하나의 정제된 텍스트로 합침.

    - 페이지 머리글(법제처/국가법령정보센터/법령명) 제거
    - 개정 이력 주석(<개정 …>, [전문개정 …] 등) 제거
    - PDF 강제 줄바꿈을 이어 붙이고, 조문 사이는 빈 줄(\\n\\n)로 통일
    """
    text = "\n".join(_PAGE_HEADER.sub("", page, count=1) for page in pages)
    text = _ANGLE_NOTE.sub("", text)
    text = _BRACKET_NOTE.sub("", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    vocab = {token for line in lines for token in line.split()}

    blocks: list[list[str]] = [[]]  # 블록(조문) 단위 → 줄 리스트
    for line in lines:
        if not line:  # 빈 줄 = 조문 경계
            if blocks[-1]:
                blocks.append([])
            continue
        if _BLOCK_START.match(line) and blocks[-1]:
            blocks.append([line])
        elif _LINE_START.match(line) or not blocks[-1]:
            blocks[-1].append(line)
        else:  # PDF 강제 줄바꿈 → 앞 줄에 이어 붙임
            blocks[-1][-1] = join_wrapped(blocks[-1][-1], line, vocab)

    return "\n\n".join("\n".join(block) for block in blocks if block)


def join_wrapped(prev: str, nxt: str, vocab: set[str]) -> str:
    """강제 줄바꿈된 두 줄을 이음.

    PDF는 글자 폭 기준으로 줄을 나눠 단어 중간("특허\\n에")과 단어 사이
    ("이바지함을\\n목적으로")가 섞여 있음. 경계의 두 토큰을 붙인 형태가 문서
    어휘에 있거나 다음 줄이 문장부호로 시작하면 붙이고, 아니면 공백으로 이음.
    """
    head, tail = prev.rsplit(" ", 1)[-1], nxt.split(" ", 1)[0]
    if nxt[0] in ".,)」”’" or head + tail in vocab:
        return prev + nxt
    return prev + " " + nxt


# ---------------------------------------------------------------------------
# 3. 청킹 + 메타데이터
# ---------------------------------------------------------------------------
def build_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=SEPARATORS,
        is_separator_regex=True,
        keep_separator="start",
    )


def split_text(source: str, text: str) -> list[Document]:
    """정제된 텍스트를 청크로 나누고 메타데이터를 붙임."""
    pieces = build_splitter().split_text(text)
    total = len(pieces)
    return [
        Document(
            page_content=piece,
            metadata={
                "source": source,
                "chunk_index": i,
                "total_chunks": total,
                "char_count": len(piece),
            },
        )
        for i, piece in enumerate(pieces)
    ]


def prepare_chunks(data_dir: Path = DATA_DIR) -> list[Document]:
    """로드 → 정제 → 청킹을 모든 PDF에 대해 수행함."""
    chunks: list[Document] = []
    for source, pages in load_pdfs(data_dir).items():
        text = clean_text([page.page_content for page in pages])
        file_chunks = split_text(source, text)
        print(f"[load/clean/split] {source}: {len(pages)}페이지, "
              f"{len(text):,}자 → {len(file_chunks)}개 청크")
        chunks.extend(file_chunks)
    return chunks


# ---------------------------------------------------------------------------
# 4~5. 임베딩 + 벡터 DB 저장
# ---------------------------------------------------------------------------
def get_embeddings() -> CohereEmbeddings:
    load_dotenv(ENV_FILE)
    if not os.getenv("COHERE_API_KEY"):
        raise EnvironmentError(f"COHERE_API_KEY가 없습니다. {ENV_FILE}를 확인하세요.")
    return CohereEmbeddings(model=EMBEDDING_MODEL)


def get_vectorstore(
    embeddings: CohereEmbeddings | None = None,
    collection_name: str = COLLECTION_NAME,
    client: chromadb.ClientAPI | None = None,
) -> Chroma:
    """영속화된 ChromaDB 컬렉션을 엶(없으면 생성)."""
    return Chroma(
        collection_name=collection_name,
        embedding_function=embeddings or get_embeddings(),
        client=client or chromadb.PersistentClient(path=str(VECTORDB_DIR)),
        collection_metadata={"hnsw:space": "cosine", "embedding_model": EMBEDDING_MODEL},
    )


def add_with_rate_limit(store: Chroma, docs: list[Document], ids: list[str]) -> None:
    """배치 하나를 저장함. Cohere 분당 토큰 한도(429)에 걸리면 1분 쉬고 재시도."""
    for attempt in range(1, RATE_LIMIT_RETRIES + 1):
        try:
            store.add_documents(docs, ids=ids)
            return
        except TooManyRequestsError:
            if attempt == RATE_LIMIT_RETRIES:
                raise
            print(f"[rate-limit] Cohere 호출 한도 초과 → {RATE_LIMIT_WAIT_SEC}초 대기 후 "
                  f"재시도 ({attempt}/{RATE_LIMIT_RETRIES - 1})")
            time.sleep(RATE_LIMIT_WAIT_SEC)


def remove_orphan_segments(db_dir: Path | None = None) -> list[str]:
    """삭제된 컬렉션이 남긴 HNSW 세그먼트 디렉토리를 정리함.

    Chroma는 delete_collection 후에도(특히 Windows) 세그먼트 폴더를 남길 수 있음.
    chroma.sqlite3의 segments 테이블에 없는 UUID 폴더만 지우므로
    공용 DB에 있는 다른 컬렉션은 건드리지 않음.
    """
    db_dir = db_dir or VECTORDB_DIR
    sqlite_path = db_dir / "chroma.sqlite3"
    if not sqlite_path.exists():
        return []
    with closing(sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)) as con:
        live = {row[0] for row in con.execute("SELECT id FROM segments")}
    removed = []
    for path in db_dir.iterdir():
        if path.is_dir() and _UUID.fullmatch(path.name) and path.name not in live:
            shutil.rmtree(path, ignore_errors=True)
            if not path.exists():
                removed.append(path.name)
    return removed


def build_vectorstore(chunks: list[Document]) -> Chroma:
    """청크를 배치 단위로 임베딩·저장함.

    스테이징 컬렉션에 모두 저장한 뒤에만 기존 컬렉션과 교체하므로,
    중간에 실패해도 기존 벡터 DB는 그대로 유지됨.
    """
    embeddings = get_embeddings()
    if removed := remove_orphan_segments():
        print(f"[cleanup] 사용하지 않는 세그먼트 {len(removed)}개 삭제")
    client = chromadb.PersistentClient(path=str(VECTORDB_DIR))
    staging_name = f"{COLLECTION_NAME}__staging"
    if staging_name in [c.name for c in client.list_collections()]:
        client.delete_collection(staging_name)  # 이전 실패 잔여물 정리
    staging = get_vectorstore(embeddings, staging_name, client)

    ids = [f"{c.metadata['source']}::{c.metadata['chunk_index']:05d}" for c in chunks]
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        end = start + EMBED_BATCH_SIZE
        add_with_rate_limit(staging, chunks[start:end], ids[start:end])
        print(f"[embed/store] {min(end, len(chunks))}/{len(chunks)}")

    # 스테이징 → 운영 컬렉션 교체
    if COLLECTION_NAME in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION_NAME)
    client.get_collection(staging_name).modify(name=COLLECTION_NAME)
    return get_vectorstore(embeddings, COLLECTION_NAME, client)


def main() -> int:
    parser = argparse.ArgumentParser(description="특허법 PDF 인덱싱")
    parser.add_argument("--dry-run", action="store_true",
                        help="임베딩/저장 없이 정제·청킹 결과만 출력")
    args = parser.parse_args()

    chunks = prepare_chunks()
    sizes = [c.metadata["char_count"] for c in chunks]
    print(f"[summary] 청크 {len(chunks)}개, 평균 {sum(sizes) / len(sizes):.0f}자, "
          f"최소 {min(sizes)}자, 최대 {max(sizes)}자")

    if args.dry_run:
        for chunk in chunks[:3]:
            print("-" * 60, chunk.metadata, chunk.page_content, sep="\n")
        return 0

    store = build_vectorstore(chunks)
    print(f"[done] {VECTORDB_DIR} / '{COLLECTION_NAME}' 컬렉션에 "
          f"{store._collection.count()}개 저장")

    query = "특허를 받을 수 있는 조건은?"
    print(f"\n[check] '{query}' 검색 결과 Top 3")
    for doc, score in store.similarity_search_with_score(query, k=3):
        preview = doc.page_content[:80].replace("\n", " ")
        print(f"  - distance={score:.4f} chunk={doc.metadata['chunk_index']} | {preview}…")

    # Chroma 파일 핸들을 해제해야(Windows) 교체 전 컬렉션의 세그먼트 폴더를 지울 수 있음
    del store
    SharedSystemClient.clear_system_cache()
    gc.collect()
    if removed := remove_orphan_segments():
        print(f"\n[cleanup] 사용하지 않는 세그먼트 {len(removed)}개 삭제")
    return 0


if __name__ == "__main__":
    sys.exit(main())
