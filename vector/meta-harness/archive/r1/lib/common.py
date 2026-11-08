"""하네스 실습 공통 모듈.

- 검색기: vector/hybrid-search/hybrid_rag.py의 함수를 import해 기존 인덱스(vectordb/, bm25/)를 그대로 씀(인덱싱 없음)
- 모의 전송함: 메일 도구는 실제로 보내지 않고 outbox/에 JSON 파일만 씀
- 실행 기록(RunRecord): 답, 도구 호출, 모델 호출 수, 토큰, 시간, 중단 사유, 실패 유형
- 사용량 집계(UsageCallback): 에이전트 안팎의 모든 LLM 호출 토큰을 콜백으로 셈(예외로 끊겨도 남음)

원래 hybrid-search 코드는 고치지 않고 읽기만 함.
"""

from __future__ import annotations

import json
import re
import sys
import time
import uuid
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.documents import Document

HARNESS_DIR = Path(__file__).resolve().parent
HYBRID_DIR = HARNESS_DIR.parent / "hybrid-search"
OUTBOX_DIR = HARNESS_DIR / "outbox"
RESULTS_DIR = HARNESS_DIR / "results"

sys.path.insert(0, str(HYBRID_DIR))
import hybrid_rag as rag  # noqa: E402  (원래 Hybrid Search 프로그램: 검색기·인덱스 로드 재사용)

NOT_FOUND = "문서에서 찾지 못했습니다"
ARTICLE_RE = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")


def setup_console() -> None:
    """Windows 콘솔에서 한글이 깨지지 않게 표준 출력을 UTF-8로 맞춤."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def articles_in(text: str) -> set[str]:
    """글에 나온 조문 번호 집합. 예: '제88조제1항', '제30조의2' → {'제88조', '제30조의2'}"""
    return {f"제{n}조" + (f"의{s}" if s else "") for n, s in ARTICLE_RE.findall(text or "")}


# ---------------------------------------------------------------------------
# 검색기 (hybrid_rag 재사용)
# ---------------------------------------------------------------------------
class Retrieval:
    """BM25 + 벡터 Hybrid 검색기를 한 번만 로드해 재사용함."""

    def __init__(self) -> None:
        rag.load_env()
        store = rag.load_vectorstore()
        bm25 = rag.load_bm25_retriever(k=rag.FETCH_K)
        rag.check_same_corpus(store, bm25)
        vector = rag.build_vector_retriever(store, rag.FETCH_K)
        self.hybrid = rag.build_hybrid_retriever(bm25, vector, rag.BM25_WEIGHT, rag.TOP_K)
        # 근거 대조(3단계)와 채점에 쓸 청크 원문: chunk_index → 본문 (BM25 코퍼스와 벡터 DB는 같은 청크)
        self.chunks: dict[int, str] = {d.metadata["chunk_index"]: d.page_content for d in bm25.docs}

    def search(self, query: str) -> list[Document]:
        return self.hybrid.invoke(query)


@lru_cache(maxsize=1)
def get_retrieval() -> Retrieval:
    return Retrieval()


def make_llm(timeout: float | None = None):
    """Groq gpt-oss-120b. reasoning_effort=high면 본문이 비는 일이 있어 medium(과거 실습 교훈)."""
    from langchain_groq import ChatGroq

    rag.load_env()   # .env의 GROQ_API_KEY
    return ChatGroq(model=rag.LLM_MODEL, temperature=0, reasoning_effort="medium",
                    max_retries=2, timeout=timeout)


# ---------------------------------------------------------------------------
# 실행 중 기록 (도구 함수가 직접 남김)
# ---------------------------------------------------------------------------
@dataclass
class Tracker:
    retrieved: list[int] = field(default_factory=list)      # 이번 실행에서 검색된 chunk_index(순서 유지)
    tool_calls: list[dict] = field(default_factory=list)    # {"name", "args", "ok", "note"}
    sent: list[dict] = field(default_factory=list)          # 실제로 outbox에 쓴 메일
    events: list[dict] = field(default_factory=list)        # 하네스 장치가 남긴 사건(차단·중단·승인)
    started: float = field(default_factory=time.time)

    def tool(self, name: str, args: dict, ok: bool = True, note: str = "") -> None:
        self.tool_calls.append({"name": name, "args": args, "ok": ok, "note": note})

    def event(self, kind: str, **detail: Any) -> None:
        self.events.append({"kind": kind, **detail})


_TRACKER: ContextVar[Tracker] = ContextVar("tracker")


def tracker() -> Tracker:
    try:
        return _TRACKER.get()
    except LookupError:   # 실행 밖(테스트 등)에서 도구를 직접 부를 때
        t = Tracker()
        _TRACKER.set(t)
        return t


def new_tracker() -> Tracker:
    t = Tracker()
    _TRACKER.set(t)
    return t


def record_search(query: str) -> list[Document]:
    """검색하고 결과 chunk_index를 기록함."""
    docs = get_retrieval().search(query)
    t = tracker()
    for d in docs:
        i = d.metadata.get("chunk_index")
        if i not in t.retrieved:
            t.retrieved.append(i)
    t.tool("search", {"query": query}, ok=bool(docs), note="" if docs else "결과 없음")
    return docs


# ---------------------------------------------------------------------------
# 모의 전송함 (외부 전송 도구 대신)
# ---------------------------------------------------------------------------
def send_mail(to: str, subject: str, body: str) -> Path:
    """실제 메일을 보내지 않고 outbox/에 JSON으로 남김. 4단계 승인 확인의 기준."""
    OUTBOX_DIR.mkdir(exist_ok=True)
    path = OUTBOX_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.json"
    mail = {"to": to, "subject": subject, "body": body}
    path.write_text(json.dumps(mail, ensure_ascii=False, indent=2), encoding="utf-8")
    tracker().sent.append({**mail, "file": path.name})
    return path


def outbox_files() -> list[Path]:
    return sorted(OUTBOX_DIR.glob("*.json")) if OUTBOX_DIR.exists() else []


# ---------------------------------------------------------------------------
# 사용량 집계 콜백
# ---------------------------------------------------------------------------
class UsageCallback(BaseCallbackHandler):
    """모든 LLM 호출의 입력·출력 토큰과 호출 수를 셈."""

    def __init__(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def on_llm_end(self, response, **kwargs) -> None:
        self.calls += 1
        for gens in response.generations:
            for g in gens:
                usage = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                self.input_tokens += usage.get("input_tokens", 0)
                self.output_tokens += usage.get("output_tokens", 0)


# ---------------------------------------------------------------------------
# 실행 기록
# ---------------------------------------------------------------------------
@dataclass
class RunRecord:
    stage: int
    question: str
    answer: str = ""
    sources: list[int] = field(default_factory=list)      # 답이 인용한 청크(3단계부터 스키마로 받음)
    retrieved: list[int] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    sent: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    stopped: str = ""          # 하네스가 멈춘 이유(loop_limit, tool_limit, repeat, budget, timeout, runaway)
    pending_approval: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def print_record(rec: RunRecord) -> None:
    print(f"\n[{rec.stage}단계 답변]\n{rec.answer.strip()}\n")
    if rec.sources:
        print(f"[인용 청크] {', '.join(f'#{i}' for i in rec.sources)}")
    print(f"[검색된 청크] {', '.join(f'#{i}' for i in rec.retrieved) or '-'}")
    print(f"[도구 호출 {len(rec.tool_calls)}회] " + ", ".join(
        f"{c['name']}{'' if c['ok'] else '(실패)'}" for c in rec.tool_calls))
    print(f"[모델 호출] {rec.model_calls}회 | [토큰] 입력 {rec.input_tokens:,} / 출력 {rec.output_tokens:,}"
          f" | [시간] {rec.seconds:.1f}초")
    if rec.sent:
        print(f"[전송됨] {len(rec.sent)}건 → outbox/{rec.sent[0]['file']} 외")
    if rec.stopped:
        print(f"[중단] {rec.stopped}")
    for e in rec.events:
        if e["kind"] != "tool_sig":   # 반복 감지용 내부 기록은 생략
            print(f"[하네스] {e}")
    if rec.error:
        print(f"[오류] {rec.error}")
