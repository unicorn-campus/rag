# 특허법 BM25 인덱싱

Hybrid Search에 쓸 **BM25(키워드) 인덱스**를 만드는 예제입니다.
PDF를 다시 읽거나 임베딩하지 않고, `vector/indexing`에서 구축한 공용 벡터 DB(`vectordb/`)의 청크를 그대로 가져와 **Kiwi 한국어 형태소 분석기**로 토큰화한 뒤 `bm25/`에 저장합니다.

```
vectordb/ (ChromaDB 청크) → Kiwi 형태소 토큰화 → BM25 인덱스 → bm25/ (corpus.jsonl + meta.json)
```

## 1. 목표 및 주요 기능

**목표**: 벡터 검색이 약한 "정확한 용어·조문 번호 매칭"을 BM25로 보완할 수 있도록, 벡터 DB와 **완전히 같은 청크**에 대한 BM25 인덱스를 만듭니다.

| 기능 | 설명 |
|------|------|
| 벡터 DB 청크 재사용 | ChromaDB 컬렉션 `patent_law`의 본문·메타데이터·ID를 그대로 읽음. 임베딩 API 호출 없음 (API 키 불필요) |
| 한국어 형태소 토큰화 | Kiwi로 조사·어미를 떼고 내용어만 남김. "특허를" → `특허` |
| 조문 번호 토큰 | "제42조", "제132조의2"를 한 토큰으로 추가 (Kiwi는 `제/42/조`로 쪼갬) |
| 사람이 읽을 수 있는 인덱스 | pickle 대신 JSON Lines로 저장. 청크별 토큰을 바로 열어 볼 수 있음 |
| 안전한 교체 | `bm25__staging/`에 다 쓴 뒤 교체하므로 실패해도 기존 인덱스가 남음 |
| 재현성 정보 | `meta.json`에 Kiwi 버전, 품사 목록, BM25 파라미터, 생성 시각을 기록. 검색기가 이 값으로 토크나이저 일치 여부를 검사 |

### 왜 형태소 분석이 필요한가

BM25 기본 토크나이저(`text.split()`)는 공백으로만 나눕니다. 한국어는 조사가 붙어 있어 "특허를", "특허는", "특허의"가 모두 다른 단어가 되므로 키워드가 거의 맞지 않습니다.

| 문장 | 공백 분리 | Kiwi 토큰화 (이 예제) |
|------|-----------|----------------------|
| 특허를 받을 수 있는 조건은? | `특허를`, `받을`, `수`, `있는`, `조건은?` | `특허`, `받`, `있`, `조건` |
| 특허법 제42조 출원 방법 | `특허법`, `제42조`, `출원`, `방법` | `제42조`, `특허`, `법`, `42`, `출원`, `방법` |

### 토큰화 규칙 선택 근거

정답 청크를 아는 질문 26개로 BM25 단독 검색 성능(Top-4 안에 정답이 있는 비율)을 비교해 정했습니다.

| 남기는 형태소 | Hit@4 |
|---------------|-------|
| 명사(NNG, NNP)만 | 17/26 |
| 명사 + 조문 번호 토큰 | 18/26 |
| **명사·수사·숫자·외국어·한자·어근·동사/형용사 어간 + 조문 번호 토큰 (채택)** | **20/26** |
| 위에 접미사(XSN, 예: 특허"권") 추가 | 19/26 |

## 2. 디렉토리 구조

```
rag/
├── vectordb/                  # 공용 벡터 DB (읽기 전용으로 사용)
├── bm25/                      # ← 이 예제가 만드는 BM25 인덱스
│   ├── corpus.jsonl           #    청크 1개 = 1줄 {id, page_content, metadata, tokens}
│   └── meta.json              #    토크나이저·BM25 설정, 청크 수, 생성 시각
└── vector/
    ├── indexing/              # 벡터 DB 구축 (먼저 실행)
    ├── bm25-indexing/
    │   ├── bm25_indexing.py       # BM25 인덱싱
    │   ├── test_bm25_indexing.py  # pytest 테스트
    │   ├── pytest.ini
    │   ├── requirements.txt
    │   └── README.md
    └── hybrid-search/         # 이 인덱스를 쓰는 Hybrid 검색기
```

## 3. 소스 코드 설명 (`bm25_indexing.py`)

### 처리 흐름

```
main()
 ├─ load_chunks()     vectordb/ → 'patent_law' 컬렉션 get() → Document 191개 (chunk_index 순)
 ├─ tokenize() × N    청크마다 Kiwi 형태소 분석 → 내용어 토큰 리스트
 ├─ save_index()      bm25__staging/에 corpus.jsonl + meta.json 작성 → bm25/로 교체
 └─ [check]           load_index() → build_retriever() → 샘플 질의로 검색 확인
```

### 주요 함수

| 함수 | 역할 |
|------|------|
| `get_kiwi()` | Kiwi 모델을 한 번만 로드합니다(`lru_cache`). 로드에 약 1초가 걸립니다. |
| `tokenize(text)` | ① 정규식으로 조문 번호 토큰(`제42조`, `제132조의2`)을 만들고 ② Kiwi 결과 중 `KEEP_TAGS` 품사(NNG, NNP, NR, SN, SL, SH, XR, VV, VA)만 남깁니다. `VV-R`, `VA-I` 같은 규칙/불규칙 표시는 떼고 비교합니다. **질의 토큰화에도 같은 함수를 써야 합니다.** |
| `tokenizer_config()` | 토크나이저 설정(라이브러리 버전, 품사 목록, 조문 정규식)을 dict로 돌려줍니다. `meta.json`에 기록됩니다. |
| `load_chunks(db_dir)` | ChromaDB에서 청크 본문·메타데이터·ID를 읽어 `Document` 리스트로 만듭니다. DB/컬렉션이 없거나 비어 있으면 예외를 냅니다. |
| `build_retriever(docs, tokens, k)` | 토큰화된 코퍼스로 `BM25Okapi`를 만들고 LangChain `BM25Retriever`로 감쌉니다. |
| `save_index(docs, tokens, out_dir)` | `corpus.jsonl`, `meta.json`을 스테이징 디렉토리에 쓴 뒤 기존 인덱스와 교체합니다. |
| `load_index(index_dir)` | 저장된 인덱스를 읽어 `(docs, tokens, meta)`를 돌려주고 청크 수가 맞는지 확인합니다. |

### 설계 메모

- **같은 청크를 써야 하는 이유**: `EnsembleRetriever`는 두 검색기 결과에서 같은 청크(같은 `page_content`)의 점수를 더합니다. PDF를 따로 청킹하면 경계가 조금만 달라도 다른 문서로 취급되어 Hybrid 결합이 깨집니다.
- **문서 토큰은 저장, 질의 토큰은 실행 시 계산**: 검색기(`hybrid_rag.py`)는 저장된 토큰으로 BM25를 다시 만들고 질의만 토큰화합니다. 그래서 두 프로그램의 `KEEP_TAGS`, `ARTICLE_REF`가 같아야 하며, 검색기는 `meta.json`으로 이를 검사합니다.
- **`langchain-community` 안내**: `BM25Retriever`가 들어 있는 `langchain-community`는 2026년 6월 아카이브(유지보수 종료)되었습니다. 대체 패키지가 없어 교재와 같이 그대로 사용하며, import 시 나오는 DeprecationWarning은 코드에서 숨깁니다. 현재 버전(0.4.x)은 정상 동작합니다.

## 4. 가상환경 설정 및 패키지 설치

> Python 3.13에서 검증했습니다. 아래 명령은 모두 `rag/` 루트에서 시작합니다.

### Windows - PowerShell

```powershell
cd vector\bm25-indexing
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> `Activate.ps1` 실행이 막히면 먼저 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`를 실행하세요.

### Windows - Git Bash

```bash
cd vector/bm25-indexing
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
```

> Git Bash에서 한글이 깨지면 `export PYTHONIOENCODING=utf-8`을 먼저 실행하세요.

### macOS / Linux

```bash
cd vector/bm25-indexing
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 5. 실행 방법

### 0. 사전 조건

- `vectordb/`가 있어야 합니다. 없으면 먼저 `vector/indexing`을 실행하세요(`vector/indexing/README.md` 참고).
- API 키는 필요 없습니다(임베딩을 하지 않음).

### 1. 토큰화 결과 미리 보기 (저장 안 함)

```bash
python bm25_indexing.py --dry-run
```

### 2. 인덱스 생성

```bash
python bm25_indexing.py
```

다른 위치에 만들려면 `--out` 옵션을 씁니다.

```bash
python bm25_indexing.py --out ../../bm25_test
```

실행 예:

```
[load] C:\...\rag\vectordb / 'patent_law': 191개 청크
[tokenize] Kiwi 0.24.0: 청크당 평균 150토큰, 어휘 1,664개
[save] C:\...\rag\bm25 (corpus.jsonl, meta.json)

[check] '특허를 받을 수 있는 조건은?' → 토큰 ['특허', '받', '있', '조건']
  - 특허법.pdf::00091 | 제107조(통상실시권 설정의 재정) ① 특허발명을 실시하려는 자는 …
  - 특허법.pdf::00012 | 제24조(중단 또는 중지의 효과) 특허에 관한 절차가 중단되거나 …
  - 특허법.pdf::00092 | ② 특허출원일부터 4년이 지나지 아니한 특허발명에 관하여는 …
```

> 이 질문은 "특허", "받", "있"처럼 거의 모든 조문에 나오는 단어뿐이라 BM25만으로는 정답(제29조, #17)을 찾지 못합니다. 의미로 찾는 벡터 검색과 합쳐야 하는 이유이며, `vector/hybrid-search`에서 확인할 수 있습니다.

> **벡터 DB를 다시 인덱싱했다면 이 프로그램도 다시 실행하세요.** 검색기는 두 인덱스의 청크 ID가 다르면 실행을 멈춥니다.

### 3. 테스트

```bash
pytest -v
```

| 구분 | 내용 | 필요 조건 |
|------|------|-----------|
| 단위 테스트 (11개) | 토크나이저(조사 제거, 조문 토큰, 불규칙 태그), 검색 순위, 저장/로드 왕복, 덮어쓰기, 손상 감지, DB 없음 | 없음 |
| 통합 테스트 (3개) | 실제 vectordb 청크 로드, 인덱싱→검색(제88조·제136조), `--dry-run` CLI | `vectordb/` |
