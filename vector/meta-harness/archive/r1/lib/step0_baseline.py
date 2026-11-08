"""0단계: 하네스 없는 기본 에이전트 (교재 67p 실습 0단계).

hybrid_rag.py는 "검색 → 답변"이 고정된 체인이라, 실습을 위해 같은 Hybrid 검색기를 도구로 쓰는
ReAct 에이전트(LangChain create_agent = LangGraph 그래프)로 감쌈. 모델은 그대로(Groq gpt-oss-120b).

하네스가 없다는 뜻
- 시스템 프롬프트는 "너는 유용한 도우미야" 한 줄 (교재 13p)
- 도구 설명은 이름 수준("검색", "보냄"), 입력은 자유 문자열
- 반복·예산 상한, 답 검증, 전송 승인, 트레이스 없음. 그래프 단계 상한도 따로 두지 않음
  (LangGraph 기본값 10,007단계만 적용되어 사실상 무제한. 여기에 걸리면 'runaway'로 기록)
- 토큰 집계 콜백은 평가용 측정이며 에이전트 동작에는 관여하지 않음

이후 단계는 이 클래스를 상속해 장치를 하나씩 더함(step1 → step5).

실행:
    python step0_baseline.py "특허권의 존속기간은?"
"""

from __future__ import annotations

import argparse
import time
import uuid

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError

import common
from common import RunRecord, UsageCallback

# Groq가 도구 호출 형식을 깨뜨리는 일시 오류(400). 하네스와 무관한 잡음이라 모든 단계에서 똑같이 처음부터 다시 시도함
GROQ_FORMAT_ERRORS = ("tool_use_failed", "output_parse_failed", "json_validate_failed")
FORMAT_RETRIES = 2


def last_answer(state: dict | None) -> str:
    """에이전트 상태에서 마지막 최종 답(도구 호출이 없는 AI 메시지)을 꺼냄."""
    if not state:
        return ""
    for m in reversed(state.get("messages", [])):
        if isinstance(m, AIMessage) and not m.tool_calls:
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


class Step0:
    stage = 0
    title = "하네스 없음"

    def __init__(self, llm=None) -> None:
        self.llm = llm or self.make_llm()
        self.agent = self.build()

    # --- 구성 요소: 다음 단계에서 하나씩 바꿔 끼움 ---------------------------
    def make_llm(self):
        return common.make_llm()

    def system_prompt(self) -> str:
        return "너는 유용한 도우미야"

    def tools(self) -> list:
        @tool
        def search(q: str) -> str:
            """검색"""
            docs = common.record_search(q)
            return "\n\n".join(f"[청크 #{d.metadata['chunk_index']}]\n{d.page_content}" for d in docs)

        @tool
        def send(to: str, body: str) -> str:
            """보냄"""
            common.send_mail(to, "(제목 없음)", body)
            common.tracker().tool("send", {"to": to})
            return "보냈습니다"

        return [search, send]

    def middleware(self) -> list:
        return []

    def response_format(self):
        return None

    def checkpointer(self):
        return None

    def build(self):
        return create_agent(self.llm, self.tools(), system_prompt=self.system_prompt(),
                            middleware=self.middleware(), response_format=self.response_format(),
                            checkpointer=self.checkpointer())

    def callbacks(self, usage: UsageCallback) -> list:
        """모든 LLM·도구 호출에 붙일 콜백. 5단계가 트레이스 콜백을 더함."""
        return [usage]

    def config(self, thread_id: str, usage: UsageCallback) -> dict:
        # 반복 상한은 두지 않음(하네스 없음). 콜백은 토큰을 세는 평가용 측정
        return {"callbacks": self.callbacks(usage), "configurable": {"thread_id": thread_id}}

    # --- 실행 -----------------------------------------------------------------
    def invoke(self, question: str, cfg: dict, rec: RunRecord) -> dict:
        """에이전트를 한 번 돌림. 4단계는 승인 대기(interrupt) 처리를 위해 이 메서드를 바꿈."""
        return self.agent.invoke({"messages": [{"role": "user", "content": question}]}, cfg)

    def finish(self, question: str, state: dict | None, rec: RunRecord, usage: UsageCallback) -> None:
        """최종 답을 정함. 1단계(부분 결과)와 3단계(스키마·근거 대조)가 이 메서드를 바꿈."""
        rec.answer = last_answer(state)

    def run(self, question: str, thread_id: str | None = None) -> RunRecord:
        rec = RunRecord(stage=self.stage, question=question)
        started = time.time()
        usage = UsageCallback()
        state = None
        for attempt in range(FORMAT_RETRIES + 1):
            t = common.new_tracker()
            cfg = self.config(f"{thread_id or uuid.uuid4().hex}-{attempt}", usage)
            rec.error, rec.stopped = "", ""
            try:
                state = self.invoke(question, cfg, rec)
                break
            except GraphRecursionError:
                rec.stopped = "runaway"   # LangGraph 기본 상한(10,007단계)에 걸림 = 스스로 멈추지 못함
                break
            except Exception as e:  # noqa: BLE001
                rec.error = f"{type(e).__name__}: {str(e)[:300]}"
                if not any(k in str(e) for k in GROQ_FORMAT_ERRORS):
                    break
                t.event("format_retry", attempt=attempt + 1)
        self.finish(question, state, rec, usage)
        t = common.tracker()
        rec.retrieved, rec.tool_calls, rec.sent = t.retrieved, t.tool_calls, t.sent
        rec.events = t.events + rec.events
        rec.model_calls, rec.input_tokens, rec.output_tokens = usage.calls, usage.input_tokens, usage.output_tokens
        rec.seconds = round(time.time() - started, 1)
        return rec


def run_cli(cls, argv: list[str] | None = None) -> int:
    common.setup_console()
    parser = argparse.ArgumentParser(description=f"{cls.stage}단계: {cls.title}")
    parser.add_argument("question", nargs="+", help="질문")
    args = parser.parse_args(argv)
    common.print_record(cls().run(" ".join(args.question)))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli(Step0))
