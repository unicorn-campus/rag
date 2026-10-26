# 특허법 Naive RAG

**LangChain**으로 만든 가장 기본적인 RAG 예제입니다.
인덱싱은 새로 하지 않고, `vector/indexing`에서 구축한 공용 벡터 DB(`vectordb/`)를 로드해 검색한 뒤 **Groq LPU의 `openai/gpt-oss-120b`**로 답변을 생성합니다.

```
Query → Retriever(ChromaDB, Top-4) → Documents → LLM(Groq gpt-oss-120b) → Response
```

| 항목 | 값 |
|------|----|
| 벡터 DB | `vectordb/` 컬렉션 `patent_law` (191개 청크, 인덱싱 예제에서 구축) |
| 질의 임베딩 | Cohere `embed-v4.0` (인덱싱과 같은 모델이어야 함) |
| 검색 | 유사도 검색 Top-K = 4 |
| LLM | Groq `openai/gpt-oss-120b`, temperature=0 |
| 실행 방식 | 질문 인자 1회 실행 또는 대화형 모드 |

## 디렉토리 구조

```
rag/
├── .env                  # COHERE_API_KEY, GROQ_API_KEY
├── vectordb/             # 공용 벡터 DB (읽기 전용으로 사용)
└── vector/
    ├── indexing/         # 벡터 DB 구축 예제 (먼저 실행)
    └── naive/
        ├── naive_rag.py      # Naive RAG
        ├── test_naive_rag.py # pytest 테스트
        ├── pytest.ini
        ├── requirements.txt
        └── README.md
```

## 처리 흐름

```
main()
 ├─ load_env()          .env에서 COHERE_API_KEY, GROQ_API_KEY 확인
 ├─ load_vectorstore()  vectordb/ 존재 → 컬렉션 존재 → 임베딩 모델 일치 → 건수 > 0 확인 후 로드
 ├─ build_retriever()   as_retriever(k=4)
 ├─ build_llm()         ChatGroq(openai/gpt-oss-120b)
 ├─ build_chain()       LCEL 체인 구성
 └─ ask() / repl()      질문 1회 또는 대화형

build_chain()의 LCEL 체인:

  question ─┬─────────────────────────────▶ question
            └─ retriever (문의 → 탐색) ────▶ docs
                                              │
            RunnablePassthrough.assign(answer=…)
              format_docs(docs) → PROMPT → ChatGroq → StrOutputParser  (생성)
                                              │
                                              ▼
                              {"question", "docs", "answer"}
```

교재의 "문탐생"(문의 → 탐색 → 생성) 단계를 그대로 따릅니다. 검색 결과(`docs`)도 결과에 남겨서 근거 문서를 함께 보여 줍니다.

## 주요 함수 (`naive_rag.py`)

| 함수 | 역할 |
|------|------|
| `load_env()` | 프로젝트 루트 `.env`를 읽고 API 키 2개가 있는지 확인합니다. |
| `load_vectorstore(db_dir)` | 기존 ChromaDB를 엽니다. LangChain `Chroma`는 컬렉션이 없으면 **빈 컬렉션을 조용히 새로 만들기** 때문에, 먼저 `chromadb` 클라이언트로 컬렉션 존재 여부, 임베딩 모델(`embed-v4.0`) 일치, 건수를 확인합니다. |
| `build_retriever(store, k)` | 유사도 검색 Top-K retriever를 만듭니다. |
| `build_llm()` | `ChatGroq(model="openai/gpt-oss-120b", temperature=0)`을 만듭니다. gpt-oss는 추론 모델이라서 추론 내용은 `additional_kwargs["reasoning_content"]`에 따로 담기고, `content`에는 최종 답변만 들어옵니다. |
| `format_docs(docs)` | 검색된 청크를 `[출처: 특허법.pdf #17]` 표기와 함께 하나의 컨텍스트로 합칩니다. |
| `build_chain(retriever, llm)` | 질문을 받아 `{"question", "docs", "answer"}`를 돌려주는 LCEL 체인을 만듭니다. |
| `ask(chain, question)` | 빈 질문을 막고 체인을 실행합니다. |
| `print_result(result)` | 답변과 근거 문서 Top-K를 출력합니다. |
| `repl(chain)` | 대화형 모드입니다. `exit`/`quit`/`q`/`종료`, Ctrl+C, Ctrl+D로 끝나고, 질문 하나가 실패해도 대화는 이어집니다. |

### 프롬프트 설계

- `<context>`에 있는 조문만 근거로 한국어로 답합니다.
- 근거 조문 번호(예: 제29조제1항)를 밝힙니다.
- **법률 용어는 원문 그대로 씁니다.** 이 제약이 없을 때 LLM이 "공지(公知)"를 "공개(公知)"로, "공연히 실시"를 "공개히 실시"로 바꿔 쓴 사례가 있었습니다.
- 컨텍스트에 답이 없으면 "제공된 특허법 문서에서 관련 내용을 찾을 수 없습니다."라고만 답합니다(환각 방지).

## 실행 방법

### 0. 사전 조건

`vectordb/`가 없으면 먼저 인덱싱 예제를 실행하세요(`vector/indexing/README.md` 참고).

### 1. 가상환경 생성 및 패키지 설치

Windows (PowerShell):

```powershell
cd vector\naive
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS / Linux:

```bash
cd vector/naive
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Python 3.13에서 검증했습니다.

### 2. 환경변수 설정

프로젝트 루트 `rag/.env`:

```text
COHERE_API_KEY=your-cohere-api-key   # 질의 임베딩용
GROQ_API_KEY=your-groq-api-key       # LLM 답변 생성용
```

### 3. 실행

질문 1회:

```bash
python naive_rag.py "특허를 받을 수 있는 조건은 ?"
```

대화형 모드(종료: `exit` 또는 `종료`):

```bash
python naive_rag.py
```

검색 청크 수 바꾸기:

```bash
python naive_rag.py -k 6 "특허권의 존속기간은?"
```

실행 예(요약):

```
[답변]
특허를 받을 수 있는 조건은 다음과 같습니다.
1. 산업상 이용할 수 있는 발명이어야 한다 - 제29조제1항
2. 출원 전에 공지(公知)되었거나 공연(公然)히 실시된 발명 등이 아니어야 한다 - 제29조제1항 각 호
3. 통상의 지식을 가진 사람이 쉽게 발명할 수 없어야 한다 - 제29조제2항
...
[근거 문서 Top 4]
  - 특허법.pdf #17 | 제29조(특허요건) ① 산업상 이용할 수 있는 발명으로서 …
  - 특허법.pdf #21 | 제31조 삭제  제32조(특허를 받을 수 없는 발명) …
  - 특허법.pdf #25 | 제37조(특허를 받을 수 있는 권리의 이전 등) …
  - 특허법.pdf #0  | 특허법 [시행 2025. 11. 11.] …
```

LLM 답변은 실행할 때마다 표현이 조금씩 다를 수 있습니다.

### 4. 테스트

```bash
pytest -v
```

| 구분 | 테스트 | API 필요 |
|------|--------|----------|
| 단위 | 컨텍스트 포맷, 체인 출력 구조·컨텍스트 주입·검색 1회, 빈 질문 거부 | X (가짜 LLM/Retriever) |
| 단위 | 대화형 모드(빈 입력·오류·종료·EOF) | X |
| 단위 | DB 없음 / 컬렉션 없음(빈 컬렉션 생성 안 함) / 모델 불일치 / 빈 컬렉션 거부 | X |
| 통합 | 기존 DB 로드(새 인덱싱 없음, 191건), Top-4 검색, 제29조 1순위 | O |
| 통합 | 교재 질의 답변(제29조·산업상 이용·공지), 존속기간(20년·제88조), 범위 밖 질문 거절, CLI 1회 실행 | O |

API 키나 `vectordb/`가 없으면 통합 테스트는 자동으로 skip됩니다.
검증 결과: **13 passed** (Windows 11, Python 3.13). LLM 비결정성을 보려고 답변 테스트 4개를 3회 반복했고, 12/12 통과했습니다.

## 참고 및 주의사항

- **Naive RAG의 한계**(교재 5.1): 질문마다 항상 검색하고, 검색 품질을 검증하지 않으며, 질의 변환이 없습니다. 예를 들어 "특허 어떻게 받어?" 같은 구어체 질문은 검색 품질이 떨어질 수 있습니다. 이후 Query Transformation, Hybrid Search, Re-ranking 예제에서 개선합니다.
- **Cohere 트라이얼 키 한도**: 질문 1개당 임베딩 호출 1회를 씁니다(월 1,000회 한도).
- `pytest.ini`는 Cohere SDK 내부에서 나는 Pydantic 사용 중단 경고만 숨깁니다. 이 프로젝트 코드와는 관계없습니다.
