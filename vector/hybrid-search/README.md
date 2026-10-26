# 특허법 Hybrid Search RAG

**BM25(키워드) 검색 + 벡터(의미) 검색**을 LangChain `EnsembleRetriever`로 합쳐 검색 품질을 높이는 RAG 예제입니다.
인덱싱은 새로 하지 않고, 공용 벡터 DB(`vectordb/`)와 BM25 인덱스(`bm25/`)를 로드해 검색한 뒤 **Groq LPU의 `openai/gpt-oss-120b`**로 답변을 생성합니다.

```
          ┌─ BM25 검색  (bm25/,     Kiwi 형태소)  Top-4 ─┐
Query ────┤                                              ├─ EnsembleRetriever ─ Top-4 ─▶ LLM ─▶ Response
          └─ 벡터 검색  (vectordb/, Cohere 임베딩) Top-4 ─┘   (가중 RRF 0.3 : 0.7)       (Groq gpt-oss-120b)
```

## 1. 목표 및 주요 기능

**목표**: Naive RAG(벡터 검색만 사용)에 키워드 검색을 더해, 조문 번호·법률 용어처럼 **정확히 일치해야 하는 질문**에서 정답 청크를 더 위로 올립니다.

| 항목 | 값 |
|------|----|
| 벡터 검색 | `vectordb/` 컬렉션 `patent_law` (191개 청크), Cohere `embed-v4.0`, Top-4 |
| BM25 검색 | `bm25/` (같은 191개 청크), Kiwi 형태소 토큰, Top-4 |
| 결합 | `langchain_classic.retrievers.EnsembleRetriever`, 가중 RRF(c=60) |
| 가중치 | **BM25 0.3 : 벡터 0.7** (실험으로 정함, 아래 5장) |
| 최종 컨텍스트 | RRF 상위 4개 청크 (Naive RAG와 같은 크기) |
| LLM | Groq `openai/gpt-oss-120b`, temperature=0 |
| 실행 방식 | 질문 1회, 대화형, 검색기 비교(`--compare`), 가중치 실험(`evaluate_weights.py`) |

| 기능 | 설명 |
|------|------|
| Hybrid 검색 | BM25와 벡터 검색 결과를 가중 RRF로 합치고 중복을 제거한 뒤 Top-K만 LLM에 넘김 |
| 인덱스 정합성 검사 | 두 인덱스의 청크 ID 집합이 같은지, BM25 토크나이저 설정이 같은지 실행 전에 확인 |
| 검색기 비교 모드 | LLM 호출 없이 BM25 / 벡터 / Hybrid Top-K를 나란히 출력 |
| 가중치 실험 | 정답을 아는 질문 56개로 가중치 0.0~1.0의 Hit@4·MRR@4를 비교 |

## 2. 디렉토리 구조

```
rag/
├── .env                       # COHERE_API_KEY, GROQ_API_KEY
├── vectordb/                  # 공용 벡터 DB (vector/indexing이 생성)
├── bm25/                      # BM25 인덱스 (vector/bm25-indexing이 생성)
└── vector/
    ├── indexing/              # 1) 벡터 DB 구축
    ├── bm25-indexing/         # 2) BM25 인덱스 구축
    ├── naive/                 #    비교용 Naive RAG
    └── hybrid-search/
        ├── hybrid_rag.py          # Hybrid Search RAG
        ├── evaluate_weights.py    # 가중치 실험
        ├── test_hybrid_rag.py     # pytest 테스트
        ├── pytest.ini
        ├── requirements.txt
        └── README.md
```

## 3. 소스 코드 설명

### 3.1 처리 흐름 (`hybrid_rag.py`)

```
main()
 ├─ load_env()                .env에서 COHERE_API_KEY, GROQ_API_KEY 확인
 ├─ load_vectorstore()        vectordb/ 존재 → 컬렉션 → 임베딩 모델 일치 → 건수 > 0 확인 후 로드
 ├─ load_bm25_retriever()     bm25/meta.json 검증(형식·컬렉션·토크나이저) → corpus.jsonl로 BM25 재구성
 ├─ check_same_corpus()       두 인덱스의 청크 ID 집합이 같은지 확인
 ├─ build_vector_retriever()  as_retriever(k=FETCH_K)
 ├─ build_hybrid_retriever()  EnsembleRetriever([BM25, 벡터], weights=[0.3, 0.7]) | Top-K
 ├─ build_llm()               ChatGroq(openai/gpt-oss-120b)
 ├─ build_chain()             LCEL 체인 구성
 └─ ask() / repl()            질문 1회 또는 대화형   (--compare면 compare_retrievers()만 실행)
```

`build_chain()`의 LCEL 체인 (Naive RAG와 같고 retriever만 Hybrid로 바뀜):

```
  question ─┬────────────────────────────────────────────────▶ question
            └─ hybrid retriever ───────────────────────────────▶ docs
                 ├─ BM25Retriever   (tokenize(질문) → BM25 점수 Top-4)
                 ├─ 벡터 retriever  (Cohere 임베딩 → 코사인 유사도 Top-4)
                 ├─ EnsembleRetriever: score(d) = Σ weight_i / (rank_i(d) + 60), 중복 제거
                 └─ 상위 4개
                                              │
            RunnablePassthrough.assign(answer=…)
              format_docs(docs) → PROMPT → ChatGroq → StrOutputParser
                                              │
                                              ▼
                              {"question", "docs", "answer"}
```

### 3.2 주요 함수 (`hybrid_rag.py`)

| 함수 | 역할 |
|------|------|
| `tokenize(text)` | 질의를 BM25 인덱싱 때와 **같은 규칙**으로 토큰화합니다(조문 번호 토큰 + Kiwi 내용어). `bm25_indexing.py`의 함수와 같아야 합니다. |
| `load_env()` | 프로젝트 루트 `.env`를 읽고 API 키 2개가 있는지 확인합니다. |
| `load_vectorstore(db_dir)` | 기존 ChromaDB를 엽니다. 컬렉션이 없을 때 빈 컬렉션이 조용히 생기지 않도록 먼저 존재 여부·임베딩 모델·건수를 확인합니다. |
| `load_bm25_retriever(index_dir, k)` | `meta.json`의 형식 버전·컬렉션·토크나이저 설정을 확인하고, 저장된 토큰으로 `BM25Okapi`를 다시 만들어 `BM25Retriever`로 감쌉니다. 인덱싱 때 계산한 토큰을 그대로 쓰므로 로드가 빠릅니다. |
| `check_same_corpus(store, bm25)` | 벡터 DB와 BM25 인덱스의 청크 ID가 같은지 확인합니다. 벡터 DB만 다시 만들고 BM25를 갱신하지 않은 경우를 잡아냅니다. |
| `build_vector_retriever(store, k)` | 유사도 검색 retriever를 만듭니다. |
| `build_ensemble(bm25, vector, bm25_weight)` | `EnsembleRetriever(retrievers=[bm25, vector], weights=[w, 1-w], c=60)`를 만듭니다. |
| `build_hybrid_retriever(bm25, vector, bm25_weight, k)` | `EnsembleRetriever`는 두 결과의 **합집합 전체**(최대 8개)를 돌려주므로, 뒤에 `RunnableLambda`를 붙여 상위 k개만 남깁니다. |
| `build_llm()` / `format_docs()` / `build_chain()` / `ask()` | Naive RAG와 같습니다. |
| `compare_retrievers(question, …)` | 검색기별로 한 번씩만 검색하고, `weighted_reciprocal_rank()`로 Hybrid 순위를 계산해 세 결과를 함께 돌려줍니다. |
| `print_result()` / `print_comparison()` / `repl()` | 출력과 대화형 모드입니다. |

### 3.3 가중치 실험 (`evaluate_weights.py`)

| 함수 | 역할 |
|------|------|
| `EVAL_SET` | 정답 청크를 아는 질문 26개. keyword(법률 용어 그대로) 14개, natural(일상어로 바꿔 씀) 12개 |
| `article_set(docs)` | 조문 제목이 있는 청크에서 조문 30개를 고정 시드로 무작위 추출해 "제N조의 내용은?" 질문을 만듭니다(article 유형). 사람이 고른 질문의 편향을 줄이기 위함입니다. |
| `evaluate(bm25, vector, questions)` | 질문마다 두 검색기를 한 번씩 호출해 결과를 캐시하고, 가중치 0.0~1.0을 바꿔 가며 RRF로 다시 합쳐 Hit@4, MRR@4를 계산합니다. |
| `search()` | Cohere 트라이얼 키 한도(분당 100회)에 걸리면 60초 쉬고 재시도합니다. |

### 3.4 `EnsembleRetriever`의 동작 (langchain_classic 1.0.x 소스 기준)

- 가중 RRF: `score(d) = Σ weight_i / (rank_i(d) + c)`, 기본 `c=60`
- 같은 청크는 `page_content`로 묶어 점수를 더함 → 두 인덱스가 **같은 청크**여야 하는 이유
- Top-K로 자르지 않고 합집합 전체를 점수순으로 반환 → 이 예제에서 Top-K로 자름

## 4. 가상환경 설정 및 패키지 설치

> Python 3.13에서 검증했습니다. 아래 명령은 모두 `rag/` 루트에서 시작합니다.

### Windows - PowerShell

```powershell
cd vector\hybrid-search
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> `Activate.ps1` 실행이 막히면 먼저 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`를 실행하세요.

### Windows - Git Bash

```bash
cd vector/hybrid-search
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
```

> Git Bash에서 한글이 깨지면 `export PYTHONIOENCODING=utf-8`을 먼저 실행하세요.

### macOS / Linux

```bash
cd vector/hybrid-search
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 환경변수

프로젝트 루트 `rag/.env`:

```text
COHERE_API_KEY=your-cohere-api-key   # 질의 임베딩용
GROQ_API_KEY=your-groq-api-key       # LLM 답변 생성용
```

> `langchain-community`(BM25Retriever)는 2026년 6월 아카이브(유지보수 종료)되었습니다. 대체 패키지가 없어 교재와 같이 그대로 사용하며, import 시 나오는 DeprecationWarning은 코드에서 숨깁니다.

## 5. 가중치 추천: BM25 0.3 : 벡터 0.7

`python evaluate_weights.py` 결과입니다(질문 56개, Top-4 기준, 정답 청크가 몇 위에 있는지 측정).

**검색기별 후보 4개 (`FETCH_K=4`, 채택)**

| BM25 : 벡터 | 전체 Hit@4 | 전체 MRR@4 | keyword MRR | natural MRR | article MRR |
|-------------|-----------:|-----------:|------------:|------------:|------------:|
| 0.0 : 1.0 (벡터만) | 98% | 0.812 | 0.845 | **0.701** | 0.842 |
| 0.1 : 0.9 | 98% | **0.850** | **0.857** | 0.604 | **0.944** |
| 0.2 : 0.8 | 98% | **0.850** | **0.857** | 0.604 | **0.944** |
| **0.3 : 0.7 (기본값)** | **98%** | **0.850** | **0.857** | 0.604 | **0.944** |
| 0.4 : 0.6 | 98% | 0.841 | 0.857 | 0.604 | 0.928 |
| 0.5 : 0.5 | 91% | 0.754 | 0.738 | 0.389 | 0.908 |
| 0.7 : 0.3 | 79% | 0.673 | 0.631 | 0.208 | 0.878 |
| 1.0 : 0.0 (BM25만) | 79% | 0.551 | 0.536 | 0.167 | 0.711 |

**검색기별 후보 10개 (`--fetch-k 10`, 비교용)**

| BM25 : 벡터 | 전체 Hit@4 | 전체 MRR@4 |
|-------------|-----------:|-----------:|
| 0.0 : 1.0 (벡터만) | 98% | 0.812 |
| 0.3 : 0.7 | 91% | 0.783 |
| 0.5 : 0.5 | 88% | 0.735 |

**해석**

1. **0.1~0.3에서 가장 좋습니다.** 벡터만 쓸 때보다 정답이 Top-4 안에 드는 비율(98%)은 그대로이고, 정답 순위(MRR)는 0.812 → 0.850으로 올라갑니다. 조문 번호 질문(article)에서 효과가 크며(0.842 → 0.944), 정답이 1위로 올라오는 경우가 많아집니다.
2. 그 구간 안에서 **0.3**을 고른 이유: 0.1~0.3은 결과가 같고, 0.4부터 떨어지기 시작합니다. 교재 가이드("자연어 질문이 주로 들어옴: BM25 0.3~0.4")와도 맞고, 키워드를 조금이라도 더 반영하는 값입니다.
3. **BM25 비중이 0.5 이상이면 오히려 나빠집니다.** 일상어 질문(natural)은 "특허", "받다"처럼 흔한 단어뿐이라 BM25가 엉뚱한 조문을 올립니다.
4. **trade-off**: 일상어 질문의 MRR은 0.701 → 0.604로 내려갑니다(정답이 Top-4 안에는 있지만 1위 → 2위로 밀리는 경우). 사용자 질문이 대부분 일상어라면 BM25 0.1 또는 벡터만 쓰는 것도 선택지입니다.
5. **후보 수가 가중치만큼 중요합니다.** 검색기별 후보를 10개로 늘리면 BM25 하위권 청크가 점수를 보태 정답을 밀어내서, 어떤 가중치도 벡터만 쓸 때보다 못합니다. 그래서 `FETCH_K=4`로 정했습니다. RRF 상수 c(1/10/30/60)는 후보 4개일 때 결과 차이가 거의 없어 기본값 60을 썼습니다.

> 질문 56개의 작은 평가 세트이고 Cohere `embed-v4.0`이 이미 강한 편이라, 문서·임베딩 모델이 바뀌면 결과도 달라질 수 있습니다. 데이터가 바뀌면 `evaluate_weights.py`를 다시 돌려 확인하세요.

## 6. 실행 방법

### 0. 사전 조건 (순서대로 1회)

1. `vectordb/` 생성: `vector/indexing`에서 `python indexing.py`
2. `bm25/` 생성: `vector/bm25-indexing`에서 `python bm25_indexing.py`

둘 중 하나라도 없거나 두 인덱스의 청크가 다르면 실행 시 안내 메시지와 함께 멈춥니다.

### 1. 질문 1회

```bash
python hybrid_rag.py "특허를 받을 수 있는 조건은 ?"
```

실행 예(요약):

```
[답변]
특허를 받을 수 있는 조건은 다음 조문에 규정되어 있습니다.
1. 산업상 이용할 수 있는 발명이어야 한다 - 제29조 제1항
2. 출원 전에 공지(公知)되었거나 공연(公然)히 실시된 발명 등이 아니어야 한다 - 제29조 제1항 제1호·제2호
3. 통상의 지식을 가진 사람이 쉽게 발명할 수 없어야 한다 - 제29조 제2항
...
[근거 문서 Top 4]
  - 특허법.pdf #17 | 제29조(특허요건) ① 산업상 이용할 수 있는 발명으로서 …
  - 특허법.pdf #21 | 제31조 삭제  제32조(특허를 받을 수 없는 발명) …
  - 특허법.pdf #25 | 제37조(특허를 받을 수 있는 권리의 이전 등) …
  - 특허법.pdf #0 | 특허법 [시행 2025. 11. 11.] …
```

### 2. 대화형 모드 (종료: `exit` 또는 `종료`)

```bash
python hybrid_rag.py
```

### 3. 검색기 비교 (LLM 호출 없음)

```bash
python hybrid_rag.py --compare "특허법 제42조 출원 방법"
```

```
[BM25 토큰] ['제42조', '특허', '법', '42', '출원', '방법']

[BM25 Top 4]
  1. #159 | 제199조(국제출원에 의한 특허출원) …
  2. #54  | 제63조의2(특허출원에 대한 정보제공) …
  3. #28  | 제42조(특허출원) …
  4. #32  | 제43조(요약서) 제42조제2항에 따른 요약서는 …
[벡터 Top 4]
  1. #28  | 제42조(특허출원) …
  ...
[Hybrid (BM25 0.3 : 벡터 0.7) Top 4]
  1. #28  | 제42조(특허출원) …
  ...
```

### 4. 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `-k` | 4 | LLM에 넘길 청크 수 |
| `--fetch-k` | 4 | 검색기별 후보 수 (`-k` 이상) |
| `-w`, `--bm25-weight` | 0.3 | BM25 가중치(0~1). 벡터 가중치는 `1 - w` |
| `--compare` | - | LLM 없이 검색 결과만 비교 |

```bash
python hybrid_rag.py -w 0.5 "특허권의 존속기간은?"     # 가중치 바꾸기
python hybrid_rag.py -w 0 --compare "정정심판"          # 벡터만 쓴 것과 같음
```

### 5. 가중치 실험

```bash
python evaluate_weights.py               # 후보 4개 (Cohere 호출 56회, LLM 호출 없음)
python evaluate_weights.py --fetch-k 10  # 후보 수 바꿔 비교
```

> Cohere 트라이얼 키는 분당 100회 제한이라 연달아 실행하면 1분 대기 후 이어서 진행합니다.

### 6. 테스트

```bash
pytest -v
```

| 구분 | 내용 | 필요 조건 |
|------|------|-----------|
| 단위 테스트 (21개) | 질의 토큰화, RRF 결합·중복 제거·Top-K, 가중치에 따른 순위 변화, RRF 공식, 가중치 범위 검사, 비교 모드, BM25 인덱스 로드·메타 검증(토크나이저·컬렉션·형식·손상), 청크 정합성, 체인, 대화형 모드, CLI 인자 검사, 평가 지표 | 없음 (가짜 LLM/Retriever) |
| 통합 테스트 (7개) | 저장된 토큰 = 질의 토크나이저 결과, 두 인덱스 청크 일치, 실제 Hybrid 검색(제29조·제42조·제88조), CLI 답변(Groq), CLI 비교 모드 | `vectordb/`, `bm25/`, API 키 |
