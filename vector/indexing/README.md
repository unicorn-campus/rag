# 특허법 PDF 인덱싱 (공용 벡터 DB 구축)

`data/` 디렉토리의 PDF(특허법)를 **LangChain**으로 정제·청킹·임베딩해 **ChromaDB**(`vectordb/`)에 저장하는 예제입니다.
여기서 만든 벡터 DB는 이후 RAG 실습(Naive RAG, Hybrid Search, Re-ranking 등)에서 공용으로 씁니다.

| 항목 | 값 |
|------|----|
| 파이프라인 | PDF 로드 → 정제 → 청킹 → 임베딩 → 벡터 DB 저장 |
| 문서 로더 | `PyPDFLoader` (langchain-community) |
| 청킹 | `RecursiveCharacterTextSplitter` (chunk_size=800, chunk_overlap=200, 법령 계층 정규식 구분자) |
| 임베딩 | Cohere `embed-v4.0` (1536차원, 다국어) |
| 벡터 DB | ChromaDB, 컬렉션 `patent_law`, 거리 함수 cosine, 저장 위치 `vectordb/` |
| 메타데이터 | `source`(파일명), `chunk_index`, `total_chunks`, `char_count` |

## 디렉토리 구조

```
rag/
├── .env                    # COHERE_API_KEY
├── data/특허법.pdf          # 임베딩 대상 문서
├── vectordb/               # ← 생성되는 ChromaDB (영속화)
└── vector/indexing/
    ├── indexing.py         # 인덱싱 파이프라인
    ├── test_indexing.py    # pytest 테스트
    ├── requirements.txt
    └── README.md
```

## 처리 흐름

```
load_pdfs()          data/*.pdf → 파일별 페이지 Document 리스트
   │
clean_text()         ① 페이지 머리글 제거 (법제처 / 국가법령정보센터 / 법령명)
   │                 ② 개정 이력 주석 제거 (<개정 …>, <신설 …>, [전문개정 …], [본조신설 …] 등)
   │                 ③ PDF 강제 줄바꿈 이어 붙이기 (join_wrapped)
   │                 ④ 조문·장·부칙 사이를 빈 줄(\n\n)로 통일
   │
split_text()         RecursiveCharacterTextSplitter로 분할 + 메타데이터 4종 부여
   │
build_vectorstore()  Cohere embed-v4.0 임베딩 → 스테이징 컬렉션에 96개씩 저장
   │                 → 모두 성공하면 기존 patent_law 컬렉션과 교체
   │
remove_orphan_segments()  교체 전 컬렉션이 남긴 세그먼트 폴더 정리
```

## 주요 함수 (`indexing.py`)

| 함수 | 역할 |
|------|------|
| `load_pdfs(data_dir)` | `data/*.pdf`를 `PyPDFLoader`로 읽어 `{파일명: [페이지 Document]}`로 반환합니다. |
| `clean_text(pages)` | 페이지 텍스트를 하나로 합치고 머리글·개정 주석을 지운 뒤, 조문 단위 블록(`\n\n`)과 항·호·목 단위 줄(`\n`) 구조로 정리합니다. |
| `join_wrapped(prev, nxt, vocab)` | PDF가 글자 폭에서 끊은 줄을 잇습니다. 경계 두 토큰을 붙인 형태가 문서 어휘에 있으면 붙이고(`특허`+`에` → `특허에`), 없으면 띄웁니다(`이바지함을`+`목적으로` → `이바지함을 목적으로`). |
| `build_splitter()` | 법령 계층 구분자를 쓰는 `RecursiveCharacterTextSplitter`를 만듭니다. |
| `split_text(source, text)` | 청크를 만들고 `source`, `chunk_index`, `total_chunks`, `char_count` 메타데이터를 붙입니다. |
| `prepare_chunks(data_dir)` | 모든 PDF에 대해 로드 → 정제 → 청킹을 수행합니다. |
| `get_embeddings()` | `.env`에서 `COHERE_API_KEY`를 읽어 `CohereEmbeddings(model="embed-v4.0")`을 만듭니다. |
| `get_vectorstore(...)` | `vectordb/`의 `patent_law` 컬렉션을 LangChain `Chroma`로 엽니다. **검색 예제에서도 이 함수로 로드하면 됩니다.** |
| `add_with_rate_limit(store, docs, ids)` | 배치 하나를 저장합니다. Cohere 429(분당 토큰 한도)가 나면 60초 쉬고 최대 3번 재시도합니다. |
| `build_vectorstore(chunks)` | 스테이징 컬렉션에 전부 저장한 뒤 운영 컬렉션과 교체합니다. 중간에 실패해도 기존 DB는 그대로 남습니다. |
| `remove_orphan_segments(db_dir)` | 삭제된 컬렉션이 남긴 HNSW 세그먼트 폴더를 지웁니다. `chroma.sqlite3`에 등록되지 않은 폴더만 지우므로 공용 DB의 다른 컬렉션은 건드리지 않습니다. |
| `main()` | 전체 실행 후 샘플 질의로 검색을 확인하고, Chroma 파일 핸들을 해제한 뒤 고아 세그먼트를 정리합니다. |

### 청킹 구분자 (법령 계층 정규식)

```python
SEPARATORS = [
    r"\n\n",               # 조문(article) 경계 - 정제 단계에서 \n\n으로 맞춤
    r"\n(?=[①-⑳])",        # 항
    r"\n(?=\d+\.\s)",      # 호
    r"\n(?=[가-하]\.\s)",  # 목
    r"\n",                 # 줄
    r"(?<=다\.)\s",        # 문장 종결("~한다. ")
    r" ",                  # 공백
    r"",                   # 문자 단위(최후 수단)
]
# is_separator_regex=True, keep_separator="start"
```

- 앞쪽 구분자부터 시도하고, 800자를 넘는 조각만 다음 구분자로 다시 나눕니다.
- lookahead(`(?=…)`)를 써서 `②`, `3.`, `가.` 같은 표지가 다음 청크 **맨 앞**에 남습니다.
- 교재 예시(`"\n제"`, `"\n①"`, `"\n②"`)와 달리 ③ 이후 항도 인식합니다. 본문 속 `제1항` 같은 표현에서는 끊기지 않습니다.
- 결과: 191개 청크(평균 591자, 최대 798자), 청크의 90% 이상이 조·항·호·목 경계에서 시작합니다(테스트로 검증).

## 실행 방법

### 1. 가상환경 생성 및 패키지 설치

Windows (PowerShell):

```powershell
cd vector\indexing
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS / Linux:

```bash
cd vector/indexing
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Python 3.13에서 검증했습니다.

### 2. 환경변수 설정

프로젝트 루트(`rag/.env`)에 Cohere API 키를 넣습니다.

```text
COHERE_API_KEY=your-cohere-api-key
```

### 3. 정제·청킹 결과 미리 보기 (API 호출 없음)

```bash
python indexing.py --dry-run
```

### 4. 벡터 DB 구축

```bash
python indexing.py
```

실행 예:

```
[load/clean/split] 특허법.pdf: 68페이지, 105,922자 → 191개 청크
[summary] 청크 191개, 평균 591자, 최소 127자, 최대 798자
[embed/store] 96/191
[embed/store] 191/191
[done] ...\rag\vectordb / 'patent_law' 컬렉션에 191개 저장

[check] '특허를 받을 수 있는 조건은?' 검색 결과 Top 3
  - distance=0.4042 chunk=17 | 제29조(특허요건) ① 산업상 이용할 수 있는 발명으로서 …
  - distance=0.4410 chunk=21 | 제31조 삭제  제32조(특허를 받을 수 없는 발명) …
  - distance=0.4478 chunk=25 | 제37조(특허를 받을 수 있는 권리의 이전 등) …

[cleanup] 사용하지 않는 세그먼트 1개 삭제
```

다시 실행하면 컬렉션을 통째로 새로 만들기 때문에 중복 저장되지 않습니다. 실행이 끝나면 `vectordb/`에는 `chroma.sqlite3`와 세그먼트 폴더 1개만 남습니다.

### 5. 테스트

```bash
pytest -v
```

| 구분 | 테스트 | API 필요 |
|------|--------|----------|
| 정제 | 머리글·개정 주석 제거, 본문 대괄호 보존, 줄 잇기, 조문 경계 | X |
| 청킹 | 800자 이하, 메타데이터 4종·값 정합성, 법령 경계 시작 비율 ≥ 90% | X |
| 저장 | 가짜 임베딩으로 구축·재구축, 고아 세그먼트 정리, 실패 시 기존 DB 보존, 429 재시도 | X |
| 통합 | 저장 개수, 1536차원, 검색 정확도(제29조·제88조·제32조) | O (`vectordb` 구축 후) |

검증 결과: **19 passed** (Windows 11, Python 3.13).

`COHERE_API_KEY`가 없거나 `vectordb/`가 없으면 통합 테스트는 자동으로 skip됩니다.

## 다른 예제에서 벡터 DB 불러오기

```python
import sys
sys.path.append("../indexing")          # 예: vector/naive/ 에서 실행할 때
from indexing import get_vectorstore

store = get_vectorstore()               # 임베딩 없이 기존 DB 로드
retriever = store.as_retriever(search_kwargs={"k": 4})
docs = retriever.invoke("특허를 받을 수 있는 조건은?")
```

직접 여는 경우에는 **같은 임베딩 모델**(`embed-v4.0`)과 같은 컬렉션 이름(`patent_law`)을 써야 합니다.

```python
from langchain_chroma import Chroma
from langchain_cohere import CohereEmbeddings

store = Chroma(
    collection_name="patent_law",
    embedding_function=CohereEmbeddings(model="embed-v4.0"),
    persist_directory="../../vectordb",
)
```

## 참고 및 주의사항

- **Cohere 트라이얼 키 한도**: 분당 100,000 토큰, 월 1,000회 호출입니다. 이 문서는 1회 구축에 한도 가까이 쓰므로 1분 안에 다시 실행하면 429가 납니다. 이때 코드가 60초 기다렸다가 자동으로 재시도합니다.
- **langchain-community 경고**: `PyPDFLoader`를 불러올 때 langchain-community가 단계적으로 지원 종료 중이라는 `DeprecationWarning`이 출력됩니다. 지금은 동작에 영향이 없습니다.
- **정제 규칙은 국가법령정보센터 PDF 형식에 맞춰져 있습니다.** 다른 형식의 PDF를 넣으면 `--dry-run`으로 결과를 먼저 확인하세요.
- 개정 이력 주석은 검색 노이즈를 줄이려고 지웠습니다. "언제 개정됐나" 같은 질문에는 답할 수 없습니다.
