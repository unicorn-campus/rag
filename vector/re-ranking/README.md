# 특허법 Re-ranking RAG

Hybrid 검색(BM25 + 벡터)으로 후보를 **넓게** 모은 뒤, **Cohere `rerank-multilingual-v3.0`** Re-ranker로 질문과 청크를 직접 비교해 **정밀하게** 다시 정렬하는 RAG 예제입니다.
인덱싱은 새로 하지 않고 공용 벡터 DB(`vectordb/`)와 BM25 인덱스(`bm25/`)를 로드하며, 답변은 **Groq LPU의 `openai/gpt-oss-120b`**가 생성합니다.

```
          ┌─ BM25 검색 (bm25/)      Top-10 ─┐
Query ────┤                                  ├─ EnsembleRetriever ─▶ 후보 최대 20개 ─▶ Cohere Re-ranker ─▶ Top-4 ─▶ LLM
          └─ 벡터 검색 (vectordb/)  Top-10 ─┘   (합집합·중복 제거)                  (rerank-multilingual-v3.0)   (Groq)
          └──────────────── 1차 검색: 넓게 (Retrieve More) ─────────────┘└──── 2차: 정밀하게 (Rerank Better) ───┘
```

## 1. 목표 및 주요 기능

**목표**: 교재의 "Retrieve More, Rerank Better" 전략을 구현합니다. 1차 검색은 빠르지만 순위가 거칠어 정답이 Top-4 밖으로 밀리는 경우가 있습니다. 후보를 넓게 가져와 Cross-Encoder 방식의 Re-ranker로 다시 정렬하면, LLM에 넘기는 청크 수(Top-4)는 그대로 두고 정답을 위로 올릴 수 있습니다.

| 항목 | 값 |
|------|----|
| 1차 검색 | BM25(`bm25/`, Kiwi 형태소) + 벡터(`vectordb/`, Cohere `embed-v4.0`), 검색기별 **Top-10** (합집합 최대 20개) |
| 결합 | `langchain_classic.retrievers.EnsembleRetriever` (BM25 0.3 : 벡터 0.7, 합집합·중복 제거) |
| Re-ranking | `langchain_cohere.CohereRerank(model="rerank-multilingual-v3.0")` + `ContextualCompressionRetriever` |
| 최종 컨텍스트 | Re-ranking 상위 4개 청크 (Naive·Hybrid 예제와 같은 크기) |
| LLM | Groq `openai/gpt-oss-120b`, temperature=0 |
| 질문 변환 (선택) | `--transform`: LLM(Groq gpt-oss-120b)이 rewrite / multi_query / hyde / step_back / decomposition / none 중 하나를 골라 질의를 만듦 (기본값: 끔) |
| 실행 방식 | 질문 1회, 대화형, Re-ranking 전후 비교(`--compare`), 질문 변환(`--transform`), 효과 실험(`evaluate_rerank.py`, `evaluate_transform.py`) |

| 기능 | 설명 |
|------|------|
| 2단계 검색 | 1차 Hybrid 검색 결과 **전체**를 Re-ranker에 넘기고, 관련도 점수 상위 K개만 LLM에 전달 |
| 관련도 점수 표시 | 각 근거 청크에 Re-ranker 점수(`relevance_score`, 0~1)를 함께 출력 |
| 전후 비교 모드 | LLM 없이 1차 검색 Top-K와 Re-ranking Top-K를 나란히 보여 주고, Re-ranker가 몇 위 청크를 끌어올렸는지 표시 |
| 인덱스 정합성 검사 | 두 인덱스의 청크 ID 집합, BM25 토크나이저 설정이 같은지 실행 전에 확인 |
| 질문 변환 | 검색 전에 LLM이 질문을 보고 변환 기법을 스스로 골라 질의를 만들고, 원 질문 + 변환 질의로 검색한 후보를 합쳐 Re-ranking |
| 변환 전후 비교 | `--transform --compare`로 변환 없음 / 있음의 Re-ranking Top-K를 나란히 출력 |
| 효과 실험 | 질문 56개로 벡터만 / Hybrid / Hybrid+Rerank를 후보 수별로 비교, 질문 변환 있음 / 없음 비교 |

## 2. 디렉토리 구조

```
rag/
├── .env                       # COHERE_API_KEY, GROQ_API_KEY
├── vectordb/                  # 공용 벡터 DB (vector/indexing이 생성)
├── bm25/                      # BM25 인덱스 (vector/bm25-indexing이 생성)
└── vector/
    ├── indexing/              # 1) 벡터 DB 구축
    ├── bm25-indexing/         # 2) BM25 인덱스 구축
    ├── naive/                 #    비교용: 벡터 검색만
    ├── hybrid-search/         #    비교용: BM25 + 벡터
    └── re-ranking/
        ├── rerank_rag.py          # Re-ranking RAG
        ├── evaluate_rerank.py     # Re-ranking 효과 / 후보 수 실험
        ├── evaluate_transform.py  # 질문 변환 있음 / 없음 비교 실험
        ├── test_rerank_rag.py     # pytest 테스트
        ├── pytest.ini
        ├── requirements.txt
        └── README.md
```

## 3. 소스 코드 설명

### 3.1 처리 흐름 (`rerank_rag.py`)

```
main()
 ├─ load_env()                .env에서 COHERE_API_KEY(임베딩·Re-ranking), GROQ_API_KEY 확인
 ├─ load_vectorstore()        vectordb/ 검증 후 로드
 ├─ load_bm25_retriever()     bm25/meta.json 검증 → corpus.jsonl로 BM25 재구성 (k=FETCH_K)
 ├─ check_same_corpus()       두 인덱스의 청크 ID 집합이 같은지 확인
 ├─ build_ensemble()          1차 검색기: EnsembleRetriever([BM25, 벡터])
 ├─ build_reranker()          CohereRerank(rerank-multilingual-v3.0, top_n=K)
 ├─ build_rerank_retriever()  ContextualCompressionRetriever(1차 검색기, Re-ranker)
 ├─ build_llm() / build_chain()
 ├─ build_transformer()       (--transform일 때) TRANSFORM_PROMPT | ChatGroq.with_structured_output(QueryPlan)
 └─ ask() / repl()            질문 1회 또는 대화형   (--compare면 compare_rerank()만 실행)
                              --transform이면 build_transform_chain(), 아니면 build_chain()
```

`build_chain()`의 LCEL 체인 (Naive·Hybrid와 같고 retriever만 바뀜):

```
  question ─┬────────────────────────────────────────────────────▶ question
            └─ ContextualCompressionRetriever ──────────────────────▶ docs
                 ├─ base_retriever = EnsembleRetriever
                 │    BM25 Top-10 ∪ 벡터 Top-10 → 중복 제거 (최대 20개)
                 └─ base_compressor = CohereRerank
                      (질문, 청크) 쌍마다 관련도 점수 → 점수순 상위 4개
                      metadata["relevance_score"] 추가
                                              │
            RunnablePassthrough.assign(answer=…)
              format_docs(docs) → PROMPT → ChatGroq → StrOutputParser
                                              │
                                              ▼
                              {"question", "docs", "answer"}
```

`--transform`일 때의 체인 (`build_transform_chain()`):

```
  question ─┬──────────────────────────────────────────────────────────────▶ question
            └─ transform_and_retrieve()
                 ├─ transformer: LLM 1회 → QueryPlan{technique, reason, queries}
                 ├─ search_queries(): [원 질문] + 변환 질의 (최대 4개)
                 ├─ first_stage.batch(): 질의마다 BM25 Top-10 ∪ 벡터 Top-10 (병렬)
                 ├─ merge_candidates(): 질의별 후보 합치기 + 중복 제거
                 └─ rerank_docs(): rerank_query() = 원 질문 + 변환 질의를 이어 붙여 1회 채점 → Top-4
                                              │
                                    plan, docs ▼
            format_docs(docs) + 원 질문 → PROMPT → ChatGroq → answer
                                              │
                                              ▼
                     {"question", "plan", "docs", "answer"}
```

### 3.2 주요 함수 (`rerank_rag.py`)

| 함수 | 역할 |
|------|------|
| `tokenize(text)` | 질의를 BM25 인덱싱 때와 같은 규칙으로 토큰화합니다(`bm25_indexing.py`와 같아야 함). |
| `load_env()` | `.env`를 읽고 API 키 2개를 확인합니다. Cohere 키 하나로 임베딩과 Re-ranking을 모두 호출합니다. |
| `load_vectorstore()` / `load_bm25_retriever()` / `check_same_corpus()` | 두 인덱스를 검증하고 로드합니다(hybrid-search와 같음). |
| `build_vector_retriever(store, k)` | 유사도 검색 retriever (k=FETCH_K). |
| `build_ensemble(bm25, vector, bm25_weight)` | 1차 검색기. 두 결과의 **합집합 전체**를 가중 RRF 순서로 돌려줍니다. Re-ranker가 모든 후보를 다시 채점하므로 가중치는 후보 구성에 영향을 주지 않고, `--compare`의 "1차 검색 순위"에만 쓰입니다. |
| `build_reranker(top_n)` | `CohereRerank(model="rerank-multilingual-v3.0", top_n=top_n)`. 한국어를 포함한 다국어 Re-ranker입니다. |
| `build_rerank_retriever(first_stage, reranker)` | `ContextualCompressionRetriever(base_compressor=reranker, base_retriever=first_stage)`. 1차 검색 → `compress_documents()`(Re-ranking) 순서로 실행됩니다. 후보가 없으면 Re-ranker를 호출하지 않습니다. |
| `build_llm()` / `format_docs()` / `build_chain()` / `ask()` | Naive·Hybrid 예제와 같습니다. |
| `compare_rerank(question, first_stage, reranker, k)` | 1차 검색을 한 번 하고, 그 후보로 Re-ranking을 한 번 해서 두 Top-K를 함께 돌려줍니다. Re-ranking 결과에는 1차 검색 순위(`first_rank`)를 붙입니다. |
| `QueryPlan` | 질문 변환 결과 스키마(pydantic). `technique`(6개 중 하나), `reason`, `queries`. |
| `build_transformer(llm)` | `TRANSFORM_PROMPT | llm.with_structured_output(QueryPlan, method="json_schema")`. 기법 선택과 질의 생성을 **LLM 1회 호출**로 처리합니다. |
| `search_queries(question, plan)` | 1차 검색 질의 목록. **원 질문을 항상 맨 앞에** 넣어, 변환이 빗나가도 원 질문의 후보는 남게 합니다. 공백·중복 제거, 변환 질의는 최대 4개. |
| `rerank_query(question, plan)` | Re-ranker 질의 = 원 질문 + 변환 질의를 줄바꿈으로 이은 문자열. 질의마다 따로 채점하면 Cohere 호출이 늘어나므로 1회로 묶습니다. |
| `merge_candidates(doc_lists)` | 질의별 1차 검색 결과를 합치고 `page_content`로 중복을 제거합니다. |
| `rerank_docs(reranker, docs, query, k)` | `CohereRerank.rerank()`로 채점해 상위 k개에 `relevance_score`를 붙여 돌려줍니다. |
| `transform_and_retrieve(...)` | 질문 변환 → 질의별 1차 검색(`batch`로 병렬) → 후보 합치기 → Re-ranking. `{plan, queries, candidates, docs}` 반환. |
| `build_transform_chain(...)` | 질문 변환 RAG 체인. 변환 질의는 검색에만 쓰고 **답변은 원 질문으로** 생성합니다. |
| `print_result()` / `print_comparison()` / `repl()` | 출력과 대화형 모드입니다. 근거 청크마다 `score=`(Re-ranker 관련도)를, 질문 변환을 쓰면 `[질문 변환]` 기법·이유·질의를 표시합니다. |

### 3.3 효과 실험 (`evaluate_rerank.py`)

| 함수 | 역할 |
|------|------|
| `EVAL_SET`, `article_set()` | hybrid-search와 같은 질문 56개(keyword 14, natural 12, 무작위 조문 번호 30) |
| `collect()` | 질문마다 BM25·벡터를 최대 후보 수(30)로 한 번 검색하고, 그 합집합 전체를 Re-ranker로 한 번 채점해 캐시합니다. |
| `evaluate()` | 후보 수(4/10/20/30)별로 Hybrid(RRF 순서)와 Hybrid+Rerank(점수 순서)의 Hit@4·MRR@4를 계산합니다. |
| `with_retry()` | Cohere 트라이얼 키 호출 한도(429)에 걸리면 60초 쉬고 다시 호출합니다. |

> Cohere Re-ranker 점수는 (질문, 청크) 쌍마다 독립적으로 계산됩니다. 같은 청크를 40개 묶음과 5개 묶음에 넣어 채점했을 때 점수가 소수점 5자리까지 같았습니다. 그래서 가장 큰 후보 묶음을 한 번만 채점하고, 작은 후보 수의 결과는 캐시된 점수로 계산합니다(API 호출 = 질문 수).

### 3.4 질문 변환 실험 (`evaluate_transform.py`)

| 함수 | 역할 |
|------|------|
| `make_plans()` | 질문 56개마다 LLM으로 QueryPlan을 만듭니다 (Groq, Cohere 호출 없음). |
| `first_stage_results()` | 모든 질문의 모든 질의(72개)를 **임베딩 1회 호출**로 묶고, `similarity_search_by_vector()` + BM25 → 가중 RRF로 1차 검색합니다. `EnsembleRetriever.invoke()`와 같은 계산입니다. |
| `evaluate()` | 질문마다 Re-ranking을 최대 2회 합니다. ① 원 질문으로 변환 후 후보 전체를 채점해, 원 질문 후보만 정렬한 것을 "변환 없음" 결과로 씀(점수는 쌍마다 독립) ② `rerank_query()`로 채점해 "변환 있음" 결과로 씀(`none`이면 ①과 같아 생략). |
| `Throttle` | Re-ranking 호출 사이에 6.5초 간격을 둬 분당 10회 한도(429)를 피합니다. |
| `report()` | 유형별 Hit/MRR, LLM이 고른 기법별 결과, 순위가 바뀐 질문 목록을 출력합니다. |

> 실험 1회의 Cohere 호출: 임베딩 1회 + Re-ranking 73회. Cohere 트라이얼 키는 **월 1,000회**(모든 API 합산) 한도가 있으니 반복 실행에 주의하세요.

### 3.5 질문 변환 설계

| 기법 | LLM이 고르는 기준 (프롬프트) | 만드는 질의 |
|------|------------------------------|-------------|
| `none` | 조문 번호·법률 용어가 이미 들어 있는 명확한 질문 | 없음 (원 질문만 검색, 기존 Re-ranking과 같음) |
| `rewrite` | 구어체·일상어라 조문 용어와 다른 질문 | 법률 용어로 바꾼 질의 1개 |
| `multi_query` | 여러 표현으로 찾아야 하거나 관련 조문이 흩어진 질문 | 다른 관점의 질의 3개 |
| `hyde` | 질문과 조문의 형식 차이가 큰 질문 | 조문처럼 쓴 가상 조문 1개 |
| `step_back` | 구체적 사례라 일반 원칙을 먼저 찾아야 하는 질문 | 상위 개념 질의 1개 |
| `decomposition` | 서로 다른 쟁점 둘 이상을 함께 묻는 복합 질문 | 하위 질문 2~4개 |

- **LLM 1회 호출로 선택 + 변환**: `TRANSFORM_PROMPT`(목표·역할·맥락·처리·출력·제약조건·예시 구조)와 구조화 출력(`QueryPlan`)으로 기법 이름·이유·질의를 한 번에 받습니다. 예시 질문은 평가 세트와 겹치지 않게 만들었습니다.
- **원 질문은 항상 검색**: 변환 질의는 후보를 더할 뿐이고, 최종 순위는 Re-ranker가 정합니다.
- **답변은 원 질문으로**: 변환 질의는 검색용이며, LLM 답변 프롬프트에는 사용자의 원 질문이 들어갑니다.
- **비용**: 질문 변환을 켜면 질문당 Groq 1회, Cohere 임베딩이 질의 수만큼(최대 5회) 늘어납니다. Re-ranking은 1회 그대로입니다.

### 3.6 LangChain 컴포넌트 동작 (소스 기준)

- `CohereRerank` (langchain-cohere 0.6): `cohere.ClientV2.rerank()`를 호출하고, 결과 문서의 `metadata["relevance_score"]`에 점수를 넣습니다. 반환 문서는 복사본이며 `Document.id`는 유지되지 않으므로, 이 예제는 `metadata["chunk_index"]`로 청크를 식별합니다.
- `ContextualCompressionRetriever` (langchain-classic 1.0): `base_retriever.invoke()` → 결과가 있으면 `base_compressor.compress_documents()`.
- `EnsembleRetriever` (langchain-classic 1.0): 가중 RRF, `page_content` 기준 중복 제거, Top-K로 자르지 않고 합집합 전체 반환.

## 4. 가상환경 설정 및 패키지 설치

> Python 3.13에서 검증했습니다. 아래 명령은 모두 `rag/` 루트에서 시작합니다.

### Windows - PowerShell

```powershell
cd vector\re-ranking
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> `Activate.ps1` 실행이 막히면 먼저 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`를 실행하세요.

### Windows - Git Bash

```bash
cd vector/re-ranking
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
```

> Git Bash에서 한글이 깨지면 `export PYTHONIOENCODING=utf-8`을 먼저 실행하세요.

### macOS / Linux

```bash
cd vector/re-ranking
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 환경변수

프로젝트 루트 `rag/.env`:

```text
COHERE_API_KEY=your-cohere-api-key   # 질의 임베딩 + Re-ranking
GROQ_API_KEY=your-groq-api-key       # LLM 답변 생성
```

> `langchain-community`(BM25Retriever)는 2026년 6월 아카이브(유지보수 종료)되었습니다. 대체 패키지가 없어 그대로 사용하며, import 시 나오는 DeprecationWarning은 코드에서 숨깁니다.

## 5. 실험 결과

### 5.1 Re-ranking 효과와 후보 수

`python evaluate_rerank.py` 결과입니다. 질문 56개(keyword 14, natural 12, 무작위 조문 번호 30), Top-4 기준이며 각 칸은 Hit@4 / MRR@4입니다.

| 방법 (검색기별 후보 수) | 전체 | keyword | natural | article |
|-------------------------|------|---------|---------|---------|
| 벡터만 (Naive RAG) | 98% / 0.811 | 100% / 0.845 | 92% / 0.694 | 100% / 0.842 |
| Hybrid (4+4, hybrid-search 예제) | 98% / 0.848 | 100% / 0.857 | 92% / 0.597 | 100% / 0.944 |
| Hybrid + Rerank (4+4) | 98% / **0.914** | 100% / 0.917 | 92% / **0.736** | 100% / 0.983 |
| **Hybrid + Rerank (10+10, 기본값)** | **98% / 0.911** | **100% / 0.917** | **92% / 0.722** | **100% / 0.983** |
| Hybrid + Rerank (20+20) | 98% / 0.908 | 100% / 0.917 | 92% / 0.708 | 100% / 0.983 |
| Hybrid + Rerank (30+30) | 98% / 0.899 | 100% / 0.917 | 92% / 0.667 | 100% / 0.983 |
| (참고) Hybrid만 (10+10) | 91% / 0.783 | 86% / 0.738 | 83% / 0.458 | 97% / 0.933 |

**해석**

1. **Re-ranking으로 정답 순위가 크게 오릅니다.** MRR이 벡터만 0.811, Hybrid 0.848에서 Re-ranking 0.911로 올랐습니다. 특히 Hybrid에서 떨어졌던 일상어 질문(natural)이 0.597에서 0.722로 회복되어, Hybrid의 약점(흔한 단어로 엉뚱한 조문이 올라옴)을 Re-ranker가 바로잡습니다.
2. **후보를 넓혀도 Re-ranking이 품질을 지킵니다.** Hybrid만 쓰면 후보를 10개로 늘릴 때 Hit가 98%에서 91%로 떨어지지만, Re-ranking을 붙이면 후보 수와 관계없이 98%를 유지합니다.
3. **이 문서에서는 "더 넓게"의 이득이 없었습니다.** 교재 권장값(50~100)과 달리, 후보를 30+30까지 늘리면 MRR이 조금씩 내려갑니다(0.914 → 0.899). 전체 청크가 191개뿐이고 1차 검색이 이미 정답을 대부분 상위 10위 안에 넣기 때문입니다. 후보가 많아지면 비슷한 조문이 늘어 Re-ranker가 헷갈릴 여지도 커집니다.
4. **기본값을 10+10으로 정한 이유**: 품질은 최고값(4+4, 0.914)과 거의 같고(0.911, 질문 1개의 순위 차이), 1차 검색이 놓친 청크를 Re-ranker가 끌어올릴 여유(합집합 최대 20개, 코퍼스의 약 10%)가 있습니다. 문서가 커지면 `--fetch-k`를 늘리고 `evaluate_rerank.py`로 다시 확인하세요.
5. **한계**: "논문으로 먼저 발표한 발명도 특허를 받을 수 있나요?"는 모든 방법이 놓쳤습니다. 정답(#20, 제30조 공지 예외)은 후보에 들어 있었지만(벡터 9위) Re-ranker가 16위로 매겼습니다. "논문 발표"와 조문 용어("간행물에 게재", "공지")가 달라서 생기는 문제로, Query Transformation(질문을 법률 용어로 바꿔 쓰기) 같은 기법이 필요한 경우입니다.

> 질문 56개의 작은 평가 세트입니다. Re-ranker 점수는 (질문, 청크) 쌍마다 독립적으로 매겨지므로(3.3 참고) 질문당 Re-ranking 호출은 1회입니다.

### 5.2 질문 변환 있음 / 없음

`python evaluate_transform.py` 결과입니다(같은 질문 56개, 1차 후보 검색기별 10개, Top-4, Hit@4 / MRR@4).

| 질문 유형 | 개수 | 변환 없음 | 변환 있음 (`--transform`) |
|-----------|-----:|-----------|---------------------------|
| 전체 | 56 | 98% / 0.911 | 98% / **0.917** |
| keyword | 14 | 100% / 0.917 | 100% / 0.917 |
| natural | 12 | 92% / 0.722 | 92% / **0.750** |
| article | 30 | 100% / 0.983 | 100% / 0.983 |

**LLM이 고른 기법**

| 기법 | 질문 수 | 질문 유형 | MRR 변환 없음 → 있음 | 좋아짐 / 나빠짐 |
|------|--------:|-----------|----------------------|-----------------|
| none | 39 | keyword 9, article 30 | 0.974 → 0.974 | 0 / 0 |
| rewrite | 17 | keyword 5, natural 12 | 0.765 → 0.784 | 2 / 0 |
| multi_query, hyde, step_back, decomposition | 0 | - | - | - |

**순위가 바뀐 질문 (2개, 모두 rewrite)**

| 질문 | 변환 질의 | 정답 순위 |
|------|-----------|-----------|
| 의약품 허가 때문에 특허를 못 쓴 기간만큼 보호 기간을 늘릴 수 있나? | 의약품 허가 지연에 따른 특허권 기간 연장 | 3위 → 2위 |
| 외국에 먼저 출원한 경우 그 날짜를 인정받을 수 있나요? | 우선권 주장에 대한 출원일 인정 여부 | 3위 → 2위 |

**해석**

1. **LLM의 판단은 대체로 합리적이었습니다.** 조문 번호 질문(article 30개)은 모두 `none`으로 두었고, keyword 질문 14개 중 9개는 `none`, 5개는 `rewrite`, 일상어 질문 12개는 모두 `rewrite`를 골랐습니다. 그래서 잘 되던 질문을 망치지 않았습니다(나빠짐 0).
2. **이 평가 세트에서는 효과가 작고 안정적이지 않습니다.** 전체 MRR이 0.911에서 0.917로 질문 2개만큼 올랐지만, 같은 질문을 다시 실행했을 때 LLM이 조금 다른 질의를 만들어 결과가 달라졌습니다. 아래 6장 4절 예시에서 "외국에 먼저 출원한 경우…"의 정답(#43, 제54조)이 Top-4 밖으로 밀렸습니다. 0.006 차이는 실행할 때마다 생기는 편차 범위 안이라, "개선된다"고 결론 내리기는 어렵습니다.
3. **지난 실험에서 놓친 질문은 여전히 놓칩니다.** "논문으로 먼저 발표한 발명도…"는 `rewrite`(예: "논문 선행공개된 발명의 신규성 요건")가 선택됐지만, 정답 조문의 핵심어인 "공지 예외"·"공지 등이 되지 아니한 발명으로 보는 경우"까지는 끌어내지 못했습니다.
4. **6개 기법 중 2개만 쓰였습니다.** 평가 질문이 대부분 쟁점이 하나인 짧은 질문이라 `rewrite`로 충분하다고 판단한 것입니다. 복합 질문 6개로 따로 확인했을 때(Groq만 호출) "민사로 할 수 있는 것과 형사로 할 수 있는 것은?"과 "출원·등록 절차와 등록 후 권리는?"은 `decomposition`으로 나눴습니다. 나머지 4개는 `rewrite`였습니다. `multi_query`, `hyde`, `step_back`은 한 번도 선택되지 않았는데, `rewrite`의 기준("구어체·일상어")이 거의 모든 질문에 들어맞기 때문으로 보입니다.
5. **기본값은 끔(off)으로 둡니다.** 이 문서와 질문에서는 이득이 불확실하고, 켜면 질문당 LLM 1회와 임베딩 호출이 늘어납니다. 복합 질문(쟁점이 여럿인 질문)이 많은 서비스라면 `--transform`을 켜고 `evaluate_transform.py`에 그런 질문을 추가해 다시 확인하세요.

> Cohere 임베딩은 같은 문장이라도 호출마다 값이 아주 조금 다릅니다(코사인 유사도 0.9998). 그래서 점수가 거의 같은 청크의 순서가 실행마다 바뀔 수 있고, LLM 질문 변환도 실행마다 조금씩 달라 결과에 편차가 생깁니다.

## 6. 실행 방법

### 0. 사전 조건 (순서대로 1회)

1. `vectordb/` 생성: `vector/indexing`에서 `python indexing.py`
2. `bm25/` 생성: `vector/bm25-indexing`에서 `python bm25_indexing.py`

### 1. 질문 1회

```bash
python rerank_rag.py "특허를 받을 수 있는 조건은 ?"
```

실행 예(요약):

```
[답변]
특허를 받을 수 있는 조건은 다음과 같습니다.
1. 산업상 이용할 수 있는 발명이어야 한다. - 제29조 제1항
2. 특허출원 전에 공지(公知)되었거나 공연(公然)히 실시된 발명 등이 아니어야 한다. - 제29조 제1항 1·2호
3. 통상의 지식을 가진 사람이 쉽게 발명할 수 없어야 한다(진보성). - 제29조 제2항
4. 다른 특허출원의 명세서·도면에 기재된 발명과 동일하지 않아야 한다. - 제29조 제3항
...
[근거 문서 Top 4] (Re-ranking 관련도 점수)
  - 특허법.pdf #17 score=0.998 | 제29조(특허요건) ① 산업상 이용할 수 있는 발명으로서 …
  - 특허법.pdf #28 score=0.993 | 제42조(특허출원) ① 특허를 받으려는 자는 …
  - 특허법.pdf #18 score=0.936 | ④ 특허출원한 발명이 다음 각 호의 요건을 모두 갖춘 실용신안등록출원의 …
  - 특허법.pdf #23 score=0.927 | 제36조(선출원) ① 동일한 발명에 대하여 다른 날에 …
```

> Naive RAG 근거 문서(#17, #21, #25, #0)에는 없던 #18(제29조 ③·④항이 이어지는 청크)이 Re-ranking으로 들어와, 특허요건(제29조) 조문이 끊기지 않고 컨텍스트에 담겼습니다.

### 2. 대화형 모드 (종료: `exit` 또는 `종료`)

```bash
python rerank_rag.py
```

### 3. Re-ranking 전후 비교 (LLM 호출 없음)

```bash
python rerank_rag.py --compare "남의 특허를 허락 없이 쓰면 어떤 처벌을 받나요?"
```

```
[질문] 남의 특허를 허락 없이 쓰면 어떤 처벌을 받나요?
[1차 검색 후보] 19개 (BM25 + 벡터 합집합)

[1차 검색 Top 4] (가중 RRF 순서)
  1. #103 | 제128조(손해배상청구권 등) ① 특허권자 또는 전용실시권자는 …
  2. #189 | 제229조의3(외국에의 특허출원 금지 또는 비밀취급명령 위반죄) …
  3. #102 | 제127조(침해로 보는 행위) …
  4. #182 | 제224조(허위표시의 금지) …

[Re-ranking Top 4] (rerank-multilingual-v3.0)
  1. #187 score=0.919 (1차 5위) | 제12장 벌칙  제225조(침해죄) ① 특허권 또는 전용실시권을 침해한 자는 7년…
  2. #188 score=0.901 (1차 7위) | 제227조(위증죄) …
  3. #128 score=0.897 (1차 11위) | 제138조(통상실시권 허락의 심판) …
  4. #189 score=0.824 (1차 2위) | 제229조의3(외국에의 특허출원 금지 또는 비밀취급명령 위반죄) …
```

1차 검색은 "특허", "침해" 같은 단어가 겹치는 제128조(손해배상, 민사)를 1위에 올렸지만, Re-ranker는 질문의 "처벌"을 이해하고 1차 5위였던 제225조(침해죄, 형사)를 1위로 올렸습니다. 1차 Top-4만 썼다면 정답이 LLM에 전달되지 않았을 질문입니다.

### 4. 질문 변환 사용 (`--transform`)

```bash
python rerank_rag.py --transform "특허 침해를 당했을 때 민사로 할 수 있는 것과 형사로 할 수 있는 것은 각각 무엇인가요?"
```

실행 예(요약):

```
[질문 변환] decomposition - 민사와 형사 두 가지 구제책을 각각 별도 조문으로 찾아야 하므로 분해가 필요합니다.
  · 특허 침해에 대한 민사소송 및 구제
  · 특허 침해에 대한 형사처벌 및 고소

[답변]
민사적 구제
1. 침해 금지·예방 청구권 (제126조 제1항·제2항)
2. 손해배상(배상액) 청구 - 고의적 침해는 손해액의 5배 이내 (⑧, ⑨)
형사적 구제
1. 침해죄 - 7년 이하의 징역 또는 1억원 이하의 벌금 (제225조 제1항), 피해자의 명시적 의사에 반하여 공소 제기 불가 (제225조 제2항)

[근거 문서 Top 4] (Re-ranking 관련도 점수)
  - 특허법.pdf #102 score=0.984 | 제127조(침해로 보는 행위) …
  - 특허법.pdf #187 score=0.978 | 제12장 벌칙  제225조(침해죄) …
  - 특허법.pdf #105 score=0.978 | ⑧ 법원은 타인의 특허권 또는 전용실시권을 침해한 행위가 고의적인 것으로 …
  - 특허법.pdf #100 score=0.977 | 제125조(특허실시보고) … (제126조 침해금지청구권이 이어지는 청크)
```

변환 전후 비교 (`--transform --compare`, 답변 생성 없음):

```bash
python rerank_rag.py -t --compare "외국에 먼저 출원한 경우 그 날짜를 인정받을 수 있나요?"
```

```
[Re-ranking Top 4] (rerank-multilingual-v3.0)
  1. #176 score=0.829 (1차 4위) | ④ 지식재산처장은 제3항에 따라 그 거부ㆍ선언 또는 인정이 …
  2. #156 score=0.730 (1차 9위) | 제194조(국제출원일의 인정 등) …
  3. #43  score=0.725 (1차 2위) | 제54조(조약에 의한 우선권 주장) …
  4. #159 score=0.505 (1차 7위) | 제199조(국제출원에 의한 특허출원) …

[질문 변환] rewrite - 질문이 구어체이며 조문 용어와 차이가 있어 법률 용어로 바꿔야 함
  · 우선권 외국 최초 출원일 인정 여부
[변환 후 1차 검색 후보] 25개 (질의 2개의 합집합)

[질문 변환 + Re-ranking Top 4]
  1. #156 score=0.989 | 제194조(국제출원일의 인정 등) …
  2. #78  score=0.985 (새로 진입) | ③ 제2항에서 "출원인으로 인하여 지연된 기간"에 관한 사항은 …
  3. #165 score=0.982 (새로 진입) | 3. 제56조제1항 각 호 외의 부분 본문 중 …
  4. #23  score=0.979 (새로 진입) | 제36조(선출원) …
```

> 이 실행에서는 질문 변환 후 정답(#43, 제54조 조약에 의한 우선권 주장)이 Top-4 밖으로 밀렸습니다. 평가 실행 때는 LLM이 "우선권 주장에 대한 출원일 인정 여부"를 만들어 3위 → 2위로 올랐던 질문입니다. 변환 질의를 이어 붙인 Re-ranking 질의에서는 점수가 0.98 근처로 몰리는 모습도 보였습니다. 질문 변환의 효과가 LLM 출력에 따라 흔들린다는 점을 보여 주는 예입니다(5.2 참고).

### 5. 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `-k` | 4 | Re-ranking 후 LLM에 넘길 청크 수 |
| `--fetch-k` | 10 | 1차 검색에서 검색기별로 가져올 후보 수 (`-k` 이상) |
| `-w`, `--bm25-weight` | 0.3 | 1차 검색 BM25 가중치(0~1). 후보 구성에는 영향 없고 `--compare`의 1차 순위에만 반영 |
| `--compare` | - | 답변 생성 없이 1차 검색과 Re-ranking 결과만 비교. `--transform`과 함께 쓰면 변환 전후도 비교 |
| `-t`, `--transform` | 끔 | 질문 변환 사용. LLM이 rewrite / multi_query / hyde / step_back / decomposition / none 중 선택 |

```bash
python rerank_rag.py --fetch-k 30 "특허권의 존속기간은?"   # 후보를 더 넓게
python rerank_rag.py -k 3 "정정심판은 누가 청구하나요?"      # LLM에 3개만 전달
```

### 6. 효과 실험

```bash
python evaluate_rerank.py      # 임베딩 56회 + Re-ranking 56회 호출, LLM 호출 없음
```

```bash
python evaluate_transform.py   # Groq 56회 + 임베딩 1회 + Re-ranking 약 73회 (약 10분)
```

> Cohere 트라이얼 키는 호출 한도가 있어 중간에 1분씩 대기할 수 있습니다.

### 7. 테스트

```bash
pytest -v
```

| 구분 | 내용 | 필요 조건 |
|------|------|-----------|
| 단위 테스트 (37개) | Re-ranker 모델·top_n 설정, 후보 전체 재정렬과 점수 부여, 빈 후보일 때 API 미호출, 가중치 범위, 전후 비교(1차 순위 표시), 체인 컨텍스트 = Re-ranking 결과, 점수 출력, 대화형 모드, CLI 인자 검사(인덱스 로드 전에 거름), BM25 인덱스 로드·메타 검증, 청크 정합성, 평가 지표·Re-ranking 시뮬레이션, 조문 질문 생성 / **질문 변환**: 알 수 없는 기법 거부, 프롬프트에 6개 기법 포함, 검색 질의 구성(원 질문 우선·중복 제거·최대 개수), Re-ranking 질의 결합, 후보 합치기, 원 질문이 놓친 청크를 변환 질의로 찾기, `none`이면 기존 Re-ranking과 같음, 답변은 원 질문으로 생성, 변환 전후 출력("새로 진입"), `-t` 옵션, 평가 보고서·호출 간격 | 없음 (가짜 Transformer/Re-ranker/LLM/Retriever) |
| 통합 테스트 (9개) | 저장된 토큰 = 질의 토크나이저 결과, 실제 Re-ranking 검색(제29조·제79조·제133조, 점수 내림차순·0~1), 1차 후보 수(FETCH_K 초과 ~ 2×FETCH_K 이하), CLI 답변(Groq), CLI 비교 모드(제225조가 1차 Top-4 밖 → Re-ranking 1위), **실제 Groq 구조화 출력이 QueryPlan 규칙을 지킴(2개), CLI `--transform` 답변(제3조)** | `vectordb/`, `bm25/`, API 키 |

> 통합 테스트 1회 실행에 Cohere 호출이 약 20회 듭니다(월 1,000회 한도).
