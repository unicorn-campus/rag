"""5단계: 트레이스 기록 (교재 54~55p, OpenTelemetry) + 평가 재실행(evaluate_stages.py).

4단계에 더하는 장치
1. 단계와 순서: 요청 하나를 root span("request")으로, LLM·도구 호출을 하위 span으로 남김(시간은 자동 기록)
2. 도구 이름·입력·결과: execute_tool span에 gen_ai.tool.name, tool.input, tool.result_ids(검색된 청크 번호)
3. 토큰과 비용: chat span에 gen_ai.usage.input_tokens / output_tokens
4. 검증 결과와 차단 사유: 하네스 장치가 남긴 사건을 root span 이벤트로(guard.stopped, guard.blocked, guard.verify_failed)
5. 사람 승인 기록: approval.wait / approval 이벤트(누가 아닌 무엇을 승인·거부했는지)

트레이스는 results/traces/stage5-YYYYMMDD.jsonl에 span 한 줄씩 씀(외부 수집기 없이 파일로 확인).
OTLP 수집기(Jaeger, Langfuse 등)로 보내려면 JsonlSpanExporter 대신 OTLPSpanExporter를 붙이면 됨.

확인(67p): 트레이스와 함께 0단계 대비 점수·토큰·실패 유형 비교 → python evaluate_stages.py

실행:
    python step5_trace.py "특허권의 존속기간은?"      # 답 + 이번 요청의 트레이스 요약
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Sequence

from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode

import common
from common import RESULTS_DIR, RunRecord, UsageCallback, rag
from step4_approval import Step4

HARNESS_VERSION = "step5-prompt-v2"   # 하네스·프롬프트 버전을 점수와 함께 남김(교재 57p)
TRACE_DIR = RESULTS_DIR / "traces"
DOC_ID_RE = re.compile(r'<doc id="(\d+)"')
EVENT_NAMES = {   # 하네스 사건 → 트레이스 이벤트 이름
    "stopped": "guard.stopped", "blocked_repeat": "guard.blocked", "blocked_tool_timeout": "guard.blocked",
    "blocked_ungrounded": "guard.blocked", "verify_failed": "guard.verify_failed", "partial_answer": "guard.partial_answer",
    "approval_wait": "approval.wait", "approval": "approval", "format_retry": "llm.format_retry",
}


class JsonlSpanExporter(SpanExporter):
    """끝난 span을 JSON 한 줄씩 파일에 덧붙임(고치지 않고 덧붙이기만: 재현·감사의 원본)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with open(self.path, "a", encoding="utf-8") as f:
            for s in spans:
                f.write(json.dumps({
                    "trace_id": f"{s.context.trace_id:032x}", "span_id": f"{s.context.span_id:016x}",
                    "parent_id": f"{s.parent.span_id:016x}" if s.parent else None,
                    "name": s.name, "start": s.start_time, "duration_ms": round((s.end_time - s.start_time) / 1e6, 1),
                    "status": s.status.status_code.name, "attributes": dict(s.attributes or {}),
                    "events": [{"name": e.name, "attributes": dict(e.attributes or {})} for e in s.events],
                }, ensure_ascii=False, default=str) + "\n")
        return SpanExportResult.SUCCESS


def otel_value(v: Any):
    """OpenTelemetry 속성은 기본형이나 기본형 목록만 받으므로 나머지는 JSON 문자열로 바꿈."""
    if isinstance(v, (str, bool, int, float)):
        return v
    if isinstance(v, (list, tuple)) and all(isinstance(x, (str, bool, int, float)) for x in v):
        return list(v)
    return json.dumps(v, ensure_ascii=False, default=str)


class OtelCallback(BaseCallbackHandler):
    """LangChain 콜백 → OpenTelemetry span. LLM 호출은 chat span, 도구 호출은 execute_tool span."""

    def __init__(self, tracer, parent_ctx) -> None:
        self.tracer, self.parent_ctx = tracer, parent_ctx
        self.spans: dict = {}

    def _start(self, run_id, name: str, attrs: dict) -> None:
        span = self.tracer.start_span(name, context=self.parent_ctx)
        span.set_attributes({k: otel_value(v) for k, v in attrs.items()})
        self.spans[run_id] = span

    def _end(self, run_id, attrs: dict | None = None, error: BaseException | None = None) -> None:
        span = self.spans.pop(run_id, None)
        if not span:
            return
        if attrs:
            span.set_attributes({k: otel_value(v) for k, v in attrs.items()})
        if error:
            span.set_status(Status(StatusCode.ERROR, f"{type(error).__name__}: {str(error)[:200]}"))
        span.end()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        self._start(run_id, f"chat {rag.LLM_MODEL}", {"gen_ai.operation.name": "chat",
                                                      "gen_ai.request.model": rag.LLM_MODEL,
                                                      "gen_ai.input.messages.count": len(messages[0]) if messages else 0})

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs) -> None:
        self._start(run_id, f"chat {rag.LLM_MODEL}", {"gen_ai.operation.name": "chat", "gen_ai.request.model": rag.LLM_MODEL})

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        usage = {"input_tokens": 0, "output_tokens": 0}
        tool_calls = []
        for gens in response.generations:
            for g in gens:
                msg = getattr(g, "message", None)
                for k in usage:
                    usage[k] += (getattr(msg, "usage_metadata", None) or {}).get(k, 0)
                tool_calls += [tc["name"] for tc in (getattr(msg, "tool_calls", None) or [])]
        self._end(run_id, {"gen_ai.usage.input_tokens": usage["input_tokens"],
                           "gen_ai.usage.output_tokens": usage["output_tokens"],
                           "gen_ai.response.tool_calls": tool_calls})

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        self._end(run_id, error=error)

    def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs) -> None:
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        self._start(run_id, f"execute_tool {name}", {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name,
                                                     "tool.input": inputs if inputs is not None else input_str})

    def on_tool_end(self, output, *, run_id, **kwargs) -> None:
        text = str(getattr(output, "content", output))
        self._end(run_id, {"tool.result_ids": [int(i) for i in DOC_ID_RE.findall(text)],
                           "tool.status": getattr(output, "status", "success")})

    def on_tool_error(self, error, *, run_id, **kwargs) -> None:
        self._end(run_id, error=error)


def make_tracer(path: Path):
    provider = TracerProvider(resource=Resource.create({"service.name": "patent-law-harness"}))
    provider.add_span_processor(SimpleSpanProcessor(JsonlSpanExporter(path)))
    return provider.get_tracer("harness", HARNESS_VERSION)


class Step5(Step4):
    stage = 5
    title = "트레이스 기록"

    def __init__(self, llm=None, approver=None, trace_file: Path | None = None) -> None:
        self.trace_file = trace_file or TRACE_DIR / f"stage5-{time.strftime('%Y%m%d')}.jsonl"
        self.tracer = make_tracer(self.trace_file)
        self._otel: OtelCallback | None = None
        super().__init__(llm, approver)

    def callbacks(self, usage: UsageCallback) -> list:
        return super().callbacks(usage) + ([self._otel] if self._otel else [])

    def run(self, question: str, thread_id: str | None = None) -> RunRecord:
        with self.tracer.start_as_current_span("request") as root:
            root.set_attributes({"harness.stage": self.stage, "harness.version": HARNESS_VERSION,
                                 "gen_ai.request.model": rag.LLM_MODEL, "request.question": question,
                                 "request.thread_id": thread_id or ""})
            self._otel = OtelCallback(self.tracer, trace.set_span_in_context(root))
            try:
                rec = super().run(question, thread_id)
            finally:
                self._otel = None
            for e in rec.events:   # 검증 결과·차단 사유·승인 기록
                if e["kind"] in EVENT_NAMES:
                    root.add_event(EVENT_NAMES[e["kind"]], {k: otel_value(v) for k, v in e.items()})
            root.set_attributes({
                "answer.sources": rec.sources, "answer.retrieved": rec.retrieved, "run.stopped": rec.stopped,
                "run.model_calls": rec.model_calls, "run.tool_calls": len(rec.tool_calls), "run.sent": len(rec.sent),
                "gen_ai.usage.input_tokens": rec.input_tokens, "gen_ai.usage.output_tokens": rec.output_tokens,
                "run.pending_approval": rec.pending_approval})
            if rec.error:
                root.set_status(Status(StatusCode.ERROR, rec.error[:200]))
            rec.events.append({"kind": "trace", "trace_id": f"{root.get_span_context().trace_id:032x}",
                               "file": self.trace_file.name})
        return rec


def print_trace(path: Path, trace_id: str) -> None:
    """한 요청의 span을 시간 순으로 보여 줌."""
    spans = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    spans = sorted((s for s in spans if s["trace_id"] == trace_id), key=lambda s: s["start"])
    print(f"\n[트레이스 {trace_id[:8]}… ({len(spans)} spans) → {path.name}]")
    for s in spans:
        a = s["attributes"]
        extra = ""
        if s["name"].startswith("chat"):
            extra = f"입력 {a.get('gen_ai.usage.input_tokens', 0):,} / 출력 {a.get('gen_ai.usage.output_tokens', 0):,} 토큰"
            if a.get("gen_ai.response.tool_calls"):
                extra += f", 도구 요청 {a['gen_ai.response.tool_calls']}"
        elif s["name"].startswith("execute_tool"):
            extra = f"입력 {a.get('tool.input')} → 청크 {a.get('tool.result_ids')}"
        indent = "" if s["parent_id"] is None else "  "
        print(f"  {indent}{s['name']:<32} {s['duration_ms']:>8.1f}ms {s['status']:<5} {extra}")
        for e in s["events"]:
            print(f"      · {e['name']}: {e['attributes']}")


def main(argv: list[str] | None = None) -> int:
    rc = 0
    common.setup_console()
    import argparse

    parser = argparse.ArgumentParser(description="5단계: 트레이스 기록")
    parser.add_argument("question", nargs="+")
    args = parser.parse_args(argv)
    agent = Step5()
    rec = agent.run(" ".join(args.question))
    common.print_record(rec)
    tr = next((e for e in rec.events if e["kind"] == "trace"), None)
    if tr:
        print_trace(agent.trace_file, tr["trace_id"])
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
