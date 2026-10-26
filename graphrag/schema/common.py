"""GraphRAG 스키마 설계 공통 모듈.

경로, 사전 검사, 청크 읽기, 장(章) 묶기, Groq LLM 생성, 사람 확인 파일 읽기를 모음.
API Key 값은 화면·로그·파일에 남기지 않음(환경변수로만 전달).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 경로 및 설정
# ---------------------------------------------------------------------------
SCHEMA_DIR = Path(__file__).resolve().parent          # rag/graphrag/schema
PROJECT_ROOT = SCHEMA_DIR.parents[1]                   # rag/
VECTORDB_DIR = PROJECT_ROOT / "vectordb"
ENV_FILE = PROJECT_ROOT / ".env"
CANDIDATES_DIR = SCHEMA_DIR / "candidates"
WORK_DIR = SCHEMA_DIR / "work"                         # 회차별 중간 산출물

COLLECTION_NAME = "patent_law"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MODEL_NAME = "openai/gpt-oss-120b"
SEED = 42

CQ_FILE = SCHEMA_DIR / "competency-questions.md"
SAMPLES_FILE = SCHEMA_DIR / "samples.json"
MERGED_JSON = CANDIDATES_DIR / "merged.json"
MERGED_MD = CANDIDATES_DIR / "merged.md"
DRAFT_FILE = SCHEMA_DIR / "schema-draft.json"          # 사람이 확정한 스키마 초안
EXPECTED_FILE = SCHEMA_DIR / "fill-test-expected.json"
REPORT_FILE = SCHEMA_DIR / "fill-test-report.md"
SCHEMA_FILE = SCHEMA_DIR / "schema.json"
DESIGN_FILE = SCHEMA_DIR / "schema-design.md"

# 장 제목: 줄 맨 앞의 "제N장" 또는 "제N장의M" 뒤에 공백과 제목이 옴(본문 속 "제2장에 따른" 같은 참조는 제외)
_CHAPTER = re.compile(r"(?m)^(제\s*\d+\s*장(?:의\s*\d+)?)\s+(\S[^\n]*)")
CONFIRMED_MARK = "상태: 확정"


# ---------------------------------------------------------------------------
# 사전 검사
# ---------------------------------------------------------------------------
def precheck(need_llm: bool = True) -> None:
    """벡터DB 컬렉션·청크 수와 GROQ_API_KEY를 확인하고, 없으면 안내 후 종료."""
    import chromadb

    load_dotenv(ENV_FILE)
    try:
        client = chromadb.PersistentClient(path=str(VECTORDB_DIR))
        count = client.get_collection(COLLECTION_NAME).count()
    except Exception:  # 컬렉션 없음, DB 파일 없음 등
        count = 0
    if count == 0:
        sys.exit(
            f"[사전 검사 실패] 벡터DB `{VECTORDB_DIR.name}/`에 `{COLLECTION_NAME}` 컬렉션이 없거나 청크가 0건임.\n"
            "  → `vector/indexing/README.md`대로 인덱싱을 먼저 수행할 것: python vector/indexing/indexing.py"
        )
    if need_llm and not os.getenv("GROQ_API_KEY", "").strip():
        sys.exit(
            "[사전 검사 실패] `.env`의 GROQ_API_KEY가 비어 있음.\n"
            "  → https://console.groq.com/keys 에서 키를 발급해 `.env`에 GROQ_API_KEY=... 로 넣을 것"
        )
    print(f"[사전 검사] 컬렉션 {COLLECTION_NAME}: 청크 {count}건, GROQ_API_KEY: {'설정됨' if need_llm else '검사 생략'}")


# ---------------------------------------------------------------------------
# 청크 읽기와 장 묶기
# ---------------------------------------------------------------------------
def load_chunks() -> list[dict[str, Any]]:
    """벡터DB에서 청크 전체를 chunk_index 순으로 읽음."""
    import chromadb

    client = chromadb.PersistentClient(path=str(VECTORDB_DIR))
    res = client.get_collection(COLLECTION_NAME).get(include=["documents", "metadatas"])
    chunks = [
        {"chunk_index": int(m["chunk_index"]), "text": d}
        for d, m in zip(res["documents"], res["metadatas"])
    ]
    return sorted(chunks, key=lambda c: c["chunk_index"])


def assign_chapters(chunks: list[dict[str, Any]]) -> dict[int, str]:
    """청크 본문의 장 제목으로 장을 정함. 제목이 없으면 바로 앞 청크의 장을 따름.

    한 청크에 장 제목이 여럿이면 마지막 제목을 씀(다음 청크로 이어지는 장이므로).
    """
    chapters: dict[int, str] = {}
    current = "장 없음"
    for c in chunks:
        found = _CHAPTER.findall(c["text"])
        if found:
            num, title = found[-1]
            current = f"{re.sub(r'\s+', '', num)} {title.strip()}"
        chapters[c["chunk_index"]] = current
    return chapters


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
def make_llm(reasoning_effort: str = "medium", max_tokens: int = 16000, json_mode: bool = True):
    """Groq(OpenAI 호환 엔드포인트)의 gpt-oss-120b를 neo4j-graphrag OpenAILLM으로 만듦."""
    from neo4j_graphrag.llm import OpenAILLM
    from neo4j_graphrag.utils.rate_limit import RetryRateLimitHandler

    load_dotenv(ENV_FILE)
    params: dict[str, Any] = {
        "temperature": 0,
        "seed": SEED,
        "max_completion_tokens": max_tokens,
        "reasoning_effort": reasoning_effort,
    }
    if json_mode:
        params["response_format"] = {"type": "json_object"}
    return OpenAILLM(
        model_name=MODEL_NAME,
        model_params=params,
        base_url=GROQ_BASE_URL,
        api_key=os.environ["GROQ_API_KEY"],
        rate_limit_handler=RetryRateLimitHandler(max_attempts=5, max_wait=60),
    )


async def ask_json(llm, prompt: str, system: str | None = None, retries: int = 3) -> Any:
    """LLM에 JSON 응답을 요청하고 파싱함. 생성 오류·파싱 실패 시 재시도.

    JSON 모드를 끈 LLM도 쓸 수 있게 응답에서 가장 바깥 `{ ... }`만 잘라 파싱함.
    """
    from neo4j_graphrag.exceptions import LLMGenerationError

    last_err: Exception | None = None
    for _ in range(retries):
        try:
            res = await llm.ainvoke(prompt, system_instruction=system)
        except LLMGenerationError as e:  # Groq json_validate_failed 등
            last_err = e
            continue
        text = res.content.strip()
        start, end = text.find("{"), text.rfind("}")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            last_err = e
    raise RuntimeError(f"LLM JSON 응답 실패: {type(last_err).__name__}")


# ---------------------------------------------------------------------------
# 사람 확인 파일 읽기
# ---------------------------------------------------------------------------
def require_confirmed(path: Path, what: str) -> str:
    """확정 표시(`상태: 확정`)가 있는 파일만 다음 단계 입력으로 받음."""
    if not path.exists():
        sys.exit(f"[중단] {path.name}이(가) 없음. {what} 단계를 먼저 수행할 것")
    text = path.read_text(encoding="utf-8")
    if CONFIRMED_MARK not in text:
        sys.exit(f"[중단] {path.name}이(가) 아직 확정되지 않음. 검토 후 `{CONFIRMED_MARK}`로 바꿀 것")
    return text


def load_competency_questions() -> list[dict[str, str]]:
    """확정된 competency-questions.md의 표에서 역량질문을 읽음."""
    text = require_confirmed(CQ_FILE, "역량질문")
    rows = re.findall(r"^\|\s*(CQ\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", text, flags=re.M)
    return [{"id": i, "type": t, "question": q} for i, t, q in rows]


def examples_string(cqs: list[dict[str, str]]) -> str:
    """SchemaFromTextExtractor.run(examples=...)에 넣을 역량질문 문자열."""
    lines = ["The schema must be able to answer the following competency questions:"]
    lines += [f"- {c['question']}" for c in cqs]
    return "\n".join(lines)


def load_json_confirmed(path: Path, what: str) -> dict[str, Any]:
    """`status: 확정`인 JSON 파일만 읽음."""
    if not path.exists():
        sys.exit(f"[중단] {path.name}이(가) 없음. {what} 단계를 먼저 수행할 것")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") != "확정":
        sys.exit(f"[중단] {path.name}의 status가 '확정'이 아님. 검토 후 \"status\": \"확정\"으로 바꿀 것")
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def setup_console() -> None:
    """Windows 콘솔에서 한글이 깨지지 않게 stdout·stderr를 UTF-8로 맞춤(sys.exit 안내는 stderr로 나감)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
