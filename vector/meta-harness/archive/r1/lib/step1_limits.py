"""1단계: 반복 상한과 예산 상한 (교재 24~29p, 실행 통제 / 비용 리스크).

0단계에 더하는 장치
1. 최대 반복 횟수: 모델 호출 6회, 도구 호출 8회 (ModelCallLimitMiddleware, ToolCallLimitMiddleware)
2. 요청·단계별 타임아웃: LLM 호출 30초, 도구 1회 15초, 요청 전체 90초
3. 반복 패턴 감지: 같은 도구·같은 입력이 세 번째 나오면 실행하지 않고 멈춤
4. 예산 상한: 요청당 토큰 30,000개(입력+출력)를 넘으면 다음 모델 호출 전에 멈춤
5. 부분 결과와 사유 기록: 멈추면 지금까지 검색한 자료로 답 1회 + "(일부 범위만 확인: 사유)"

끝나는 조건을 모델에 맡기지 않고 하네스 코드(미들웨어)가 셈. 프롬프트·도구는 0단계 그대로.

실행:
    python step1_limits.py "특허법 제1조부터 마지막 조까지 하나씩 검색해서 요약해 줘"
"""

from __future__ import annotations

import contextvars
import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, ToolCallLimitMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage

import common
from common import RunRecord, UsageCallback
from step0_baseline import Step0, last_answer, run_cli

MAX_MODEL_CALLS = 6
MAX_TOOL_CALLS = 8
TOKEN_BUDGET = 30_000      # 요청당 입력+출력 토큰
LLM_TIMEOUT = 30           # 초
TOOL_TIMEOUT = 15
REQUEST_TIMEOUT = 90
REPEAT_LIMIT = 2           # 같은 호출은 2번까지, 3번째에 멈춤

STOP_REASONS = {
    "model_limit": f"모델 호출 상한 {MAX_MODEL_CALLS}회 도달",
    "tool_limit": f"도구 호출 상한 {MAX_TOOL_CALLS}회 도달",
    "repeat": "같은 도구·같은 입력 반복 감지",
    "budget": f"토큰 예산 {TOKEN_BUDGET:,}개 초과",
    "timeout": f"요청 시간 {REQUEST_TIMEOUT}초 초과",
}

PARTIAL_PROMPT = """아래는 지금까지 검색한 자료입니다. 이 자료만으로 질문에 답하세요.
자료로 확인한 범위까지만 답하고, 답 마지막 줄에 "(일부 범위만 확인: {reason})"을 붙이세요.

[질문]
{question}

[검색한 자료]
{docs}"""

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")


class LoopGuard(AgentMiddleware):
    """반복 패턴 감지, 도구 타임아웃, 요청 시간·토큰 예산 검사.

    - wrap_tool_call: 도구 실행을 가로채 같은 호출 반복을 막고 15초에 자름
    - before_model: 모델을 부르기 전마다 예산·시간·반복 감지 결과를 보고 멈출지 정함
    """

    def wrap_tool_call(self, request, handler):
        t = common.tracker()
        call = request.tool_call
        sig = f"{call['name']}:{json.dumps(call['args'], ensure_ascii=False, sort_keys=True)}"
        seen = [e for e in t.events if e["kind"] == "tool_sig" and e["sig"] == sig]
        t.event("tool_sig", sig=sig)
        if len(seen) >= REPEAT_LIMIT:
            t.event("blocked_repeat", tool=call["name"], args=call["args"])
            return ToolMessage(content="같은 입력으로 세 번째 호출이라 하네스가 실행을 멈췄습니다.",
                               tool_call_id=call["id"], name=call["name"], status="error")
        ctx = contextvars.copy_context()   # 도구 스레드에서도 같은 Tracker를 쓰도록 문맥을 복사
        future = _pool.submit(ctx.run, handler, request)
        try:
            result = future.result(timeout=TOOL_TIMEOUT)
            if isinstance(result, ToolMessage) and result.status == "error":   # 도구 오류도 기록(잘못된 호출 집계용)
                t.tool(call["name"], call["args"], ok=False, note=str(result.content)[:120])
            return result
        except FutureTimeout:
            t.event("blocked_tool_timeout", tool=call["name"], seconds=TOOL_TIMEOUT)
            t.tool(call["name"], call["args"], ok=False, note="timeout")
            return ToolMessage(content=f"도구가 {TOOL_TIMEOUT}초 안에 끝나지 않아 건너뜀. 있는 근거로 답할 것",
                               tool_call_id=call["id"], name=call["name"], status="error")

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        t = common.tracker()
        used = sum((m.usage_metadata or {}).get("total_tokens", 0)
                   for m in state["messages"] if isinstance(m, AIMessage))
        reason = None
        if any(e["kind"] == "blocked_repeat" for e in t.events):
            reason = "repeat"
        elif used > TOKEN_BUDGET:
            reason = "budget"
        elif time.time() - t.started > REQUEST_TIMEOUT:
            reason = "timeout"
        if reason:
            t.event("stopped", reason=reason, tokens=used)
            return {"jump_to": "end"}
        return None


def stop_reason(state: dict | None) -> str:
    """하네스가 멈춘 이유. LoopGuard가 남긴 기록 → 호출 상한 미들웨어가 남긴 메시지 순으로 봄."""
    stops = [e["reason"] for e in common.tracker().events if e["kind"] == "stopped"]
    if stops:
        return stops[-1]
    msgs = (state or {}).get("messages", [])
    last = msgs[-1] if msgs else None
    if isinstance(last, AIMessage) and "limit" in str(last.content).lower():
        return "model_limit" if "model call" in str(last.content).lower() else "tool_limit"
    return ""


class Step1(Step0):
    stage = 1
    title = "반복·예산 상한"

    def make_llm(self):
        return common.make_llm(timeout=LLM_TIMEOUT)

    def middleware(self) -> list:
        return [ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="end"),
                ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS, exit_behavior="end"),
                LoopGuard()]

    def partial_prompt(self, question: str, reason: str) -> str:
        chunks = common.get_retrieval().chunks if common.tracker().retrieved else {}
        docs = "\n\n".join(f"[청크 #{i}]\n{chunks[i]}" for i in common.tracker().retrieved) or "(검색한 자료 없음)"
        return PARTIAL_PROMPT.format(question=question, reason=reason, docs=docs)

    def finish(self, question: str, state: dict | None, rec: RunRecord, usage: UsageCallback) -> None:
        rec.stopped = rec.stopped or stop_reason(state)
        answer = last_answer(state)
        if "limit" in answer.lower() and "exceeded" in answer.lower():
            answer = ""   # 상한 미들웨어가 남긴 안내 문구는 답이 아님
        if (rec.stopped or rec.error) and not answer:
            reason = STOP_REASONS.get(rec.stopped, rec.stopped or "오류")
            try:   # 부분 결과: 찾은 범위까지 답하고 사유를 남김
                resp = self.llm.invoke(self.partial_prompt(question, reason), config={"callbacks": self.callbacks(usage)})
                answer = resp.content or ""
                common.tracker().event("partial_answer", reason=rec.stopped or "error")
            except Exception as e:  # noqa: BLE001
                rec.error = rec.error or f"{type(e).__name__}: {str(e)[:200]}"
        rec.answer = answer


if __name__ == "__main__":
    raise SystemExit(run_cli(Step1))
