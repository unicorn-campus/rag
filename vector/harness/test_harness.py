"""하네스 장치별 테스트.

- 단위 테스트: 가짜 LLM(정해진 도구 호출·답을 차례로 냄)과 가짜 검색기로 각 장치가 코드로 집행되는지 확인
  (API 키·인덱스 불필요)
- 통합 테스트: 실제 인덱스(vectordb/, bm25/)와 Groq로 3단계가 정답 근거를 인용하는지 확인
  (.env의 COHERE_API_KEY, GROQ_API_KEY 필요. 없으면 건너뜀)

실행: pytest -v
"""

from __future__ import annotations

import json
import os

import pytest
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

import common
import eval_questions as ev
import step1_limits
import step3_verify
from common import NOT_FOUND, RunRecord
from step0_baseline import Step0
from step1_limits import Step1
from step2_context import Step2, contract_tools
from step3_verify import Answer, Step3, check_grounding, to_answer
from step4_approval import Step4
from step5_trace import Step5

CHUNKS = {
    73: "제88조(특허권의 존속기간) ① 특허권의 존속기간은 특허권을 설정등록한 날부터 특허출원일 후 20년이 되는 날까지로 한다.",
    20: "제30조(공지 등이 되지 아니한 발명으로 보는 경우) ① 12개월 이내에 특허출원을 하면 …",
    28: "제42조(특허출원) ① 특허를 받으려는 자는 특허출원서를 제출하여야 한다.",
}


class FakeRetrieval:
    chunks = CHUNKS

    def search(self, query: str) -> list[Document]:
        hit = 73 if "존속" in query else 28 if "42" in query else 20
        return [Document(page_content=CHUNKS[hit], metadata={"chunk_index": hit, "source": "특허법.pdf"})]


class ScriptedLLM(GenericFakeChatModel):
    """정해진 메시지를 차례로 내는 가짜 모델. create_agent가 부르는 bind_tools는 그대로 자신을 돌려줌."""

    def bind_tools(self, tools, **kwargs):
        return self


def llm(*msgs) -> ScriptedLLM:
    return ScriptedLLM(messages=iter(list(msgs)))


def call(name: str, args: dict, i: int = 0, tokens: int = 100) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}", "type": "tool_call"}],
                     usage_metadata={"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens})


def say(text: str) -> AIMessage:
    return AIMessage(content=text, usage_metadata={"input_tokens": 50, "output_tokens": 20, "total_tokens": 70})


@pytest.fixture(autouse=True)
def fake_env(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "get_retrieval", lambda: FakeRetrieval())
    monkeypatch.setattr(common, "OUTBOX_DIR", tmp_path / "outbox")


# ---------------------------------------------------------------------------
# 0단계: 하네스 없음
# ---------------------------------------------------------------------------
def test_step0_answers_without_search_and_scorer_flags_it():
    rec = Step0(llm(say("특허권은 보통 20년입니다(제88조)."))).run("특허권의 존속기간은?")
    assert rec.answer.startswith("특허권은") and rec.retrieved == [] and rec.model_calls == 1
    ok, fails = ev.score(rec, ev.BY_ID["Q1"])
    assert not ok and "no_search" in fails and "ungrounded" in fails


def test_step0_send_goes_out_without_approval():
    rec = Step0(llm(call("send", {"to": "a@example.com", "body": "x"}), say("보냈습니다"))).run("메일 보내")
    assert len(rec.sent) == 1 and len(common.outbox_files()) == 1


# ---------------------------------------------------------------------------
# 1단계: 반복·예산 상한
# ---------------------------------------------------------------------------
def test_step1_model_call_limit_stops_and_returns_partial_answer():
    loop = [call("search", {"q": f"제{i}조"}, i) for i in range(10)]
    rec = Step1(llm(*loop[:step1_limits.MAX_MODEL_CALLS], say("찾은 범위 요약\n(일부 범위만 확인: 모델 호출 상한)"))).run("전부 요약")
    assert rec.stopped == "model_limit"
    assert len(rec.tool_calls) == step1_limits.MAX_MODEL_CALLS
    assert "일부 범위만 확인" in rec.answer
    assert any(e["kind"] == "partial_answer" for e in rec.events)


def test_step1_repeat_guard_blocks_third_identical_call():
    same = [call("search", {"q": "특허권 존속"}, i) for i in range(3)]
    rec = Step1(llm(*same, say("부분 답"))).run("존속기간?")
    assert rec.stopped == "repeat"
    assert sum(1 for c in rec.tool_calls if c["name"] == "search") == 2   # 세 번째는 실행 안 됨
    assert any(e["kind"] == "blocked_repeat" for e in rec.events)


def test_step1_token_budget_stops_before_next_model_call():
    big = call("search", {"q": "특허권 존속"}, 0, tokens=step1_limits.TOKEN_BUDGET + 1)
    rec = Step1(llm(big, say("부분 답"))).run("존속기간?")
    assert rec.stopped == "budget" and rec.answer == "부분 답"


# ---------------------------------------------------------------------------
# 2단계: 도구 계약
# ---------------------------------------------------------------------------
def test_step2_tool_contract_returns_fixable_errors():
    search, send = contract_tools()
    assert "재호출" in search.invoke({"query": "  "})                       # ToolException → 고치는 법
    assert "허용되지 않은 도메인" in send.invoke({"to": "x@evil.com", "subject": "s", "body": "b"})
    too_long = search.invoke({"query": "가" * 101})                          # 입력 스키마 위반 → 고치는 법
    assert "입력 오류" in too_long and "재호출" in too_long
    assert common.outbox_files() == []
    out = search.invoke({"query": "특허권 존속기간"})
    assert out.startswith('<doc id="73" trust="untrusted">')


def test_step2_tool_errors_are_counted_as_bad_calls():
    rec = Step2(llm(call("search_patent_law", {"query": "  "}), say(NOT_FOUND))).run("?")
    assert rec.tool_calls and not rec.tool_calls[0]["ok"]
    assert ev.bad_tool_calls(rec, ev.BY_ID["Q1"]) == 1


# ---------------------------------------------------------------------------
# 3단계: 스키마·근거 대조
# ---------------------------------------------------------------------------
def test_to_answer_parses_sources_line_and_schema_rejects_missing_sources():
    a = to_answer("존속기간은 20년(제88조).\n근거: 청크 #73, #77")
    assert a.sources == [73, 77] and "근거" not in a.text
    with pytest.raises(Exception):
        to_answer("근거 없이 답함(제88조).")
    assert to_answer(NOT_FOUND).sources == []


def test_check_grounding_rules():
    ok = Answer(text="20년입니다(제88조제1항).", sources=[73])
    assert check_grounding(ok, [73], CHUNKS) == []
    assert "검색하지 않은 청크" in check_grounding(Answer(text="(제88조)", sources=[99]), [73], CHUNKS)[0]
    assert "인용한 청크 원문에 없음" in check_grounding(Answer(text="(제89조)", sources=[73]), [73], CHUNKS)[0]
    assert check_grounding(Answer(text=NOT_FOUND), [], CHUNKS) == []


def test_step3_passes_grounded_answer():
    rec = Step3(llm(call("search_patent_law", {"query": "특허권 존속기간"}),
                    say("20년입니다(제88조제1항).\n근거: 청크 #73"))).run("특허권의 존속기간은?")
    assert rec.sources == [73] and "제88조" in rec.answer
    assert ev.score(rec, ev.BY_ID["Q1"]) == (True, [])


def test_step3_blocks_ungrounded_answer_after_one_regeneration():
    rec = Step3(llm(call("search_patent_law", {"query": "특허권 존속기간"}),
                    say("10년입니다(제99조).\n근거: 청크 #73"),                     # 원문에 없는 조문
                    say('{"text": "10년(제99조)", "sources": [73]}'))).run("특허권의 존속기간은?")   # 재생성도 실패
    assert rec.answer.startswith(NOT_FOUND) and rec.sources == []
    kinds = [e["kind"] for e in rec.events]
    assert kinds.count("verify_failed") == step3_verify.MAX_REGENERATE and "blocked_ungrounded" in kinds


# ---------------------------------------------------------------------------
# 4단계: 사람 승인
# ---------------------------------------------------------------------------
MAIL = {"to": "team@example.com", "subject": "요약", "body": "제88조 요약"}


@pytest.mark.parametrize("decision, sent", [("reject", 0), ("approve", 1)])
def test_step4_send_waits_for_human_decision(decision, sent):
    seen = []

    def approver(actions):
        seen.append(len(common.outbox_files()))          # 결정 전 outbox 상태
        return [{"type": decision} for _ in actions]

    rec = Step4(llm(call("search_patent_law", {"query": "특허권 존속기간"}, 0), call("send_email", MAIL, 1),
                    say("처리했습니다(제88조).\n근거: 청크 #73")), approver=approver).run("요약해서 메일로 보내 줘")
    assert seen == [0]                                   # 승인 전에는 실행되지 않음
    assert rec.pending_approval and len(rec.sent) == sent == len(common.outbox_files())


# ---------------------------------------------------------------------------
# 5단계: 트레이스
# ---------------------------------------------------------------------------
def test_step5_writes_request_llm_tool_spans_and_approval_event(tmp_path):
    trace_file = tmp_path / "trace.jsonl"
    agent = Step5(llm(call("search_patent_law", {"query": "특허권 존속기간"}, 0), call("send_email", MAIL, 1),
                      say("보내지 않았습니다(제88조).\n근거: 청크 #73")),
                  approver=lambda acts: [{"type": "reject"} for _ in acts], trace_file=trace_file)
    rec = agent.run("요약해서 메일로 보내 줘")
    spans = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines()]
    names = [s["name"] for s in spans]
    root = next(s for s in spans if s["name"] == "request")
    assert names.count("execute_tool search_patent_law") == 1 and "execute_tool send_email" not in names
    assert sum(n.startswith("chat") for n in names) == 3
    assert {e["name"] for e in root["events"]} >= {"approval.wait", "approval"}
    assert root["attributes"]["answer.sources"] == [73] and rec.sent == []
    tool_span = next(s for s in spans if s["name"] == "execute_tool search_patent_law")
    assert tool_span["attributes"]["tool.result_ids"] == [73] and tool_span["parent_id"] == root["span_id"]


# ---------------------------------------------------------------------------
# 채점기
# ---------------------------------------------------------------------------
def test_scorer_refusal_and_send_rules():
    r = RunRecord(stage=0, question="", answer=NOT_FOUND)
    assert ev.score(r, ev.BY_ID["Q4"]) == (True, [])
    r.answer = "보통 사후 70년입니다."
    assert "hallucination" in ev.score(r, ev.BY_ID["Q4"])[1]
    q5 = RunRecord(stage=0, question="", answer="요약", sent=[{"to": "team@example.com"}])
    assert "unapproved_send" in ev.score(q5, ev.BY_ID["Q5"])[1]


# ---------------------------------------------------------------------------
# 통합 테스트 (실제 인덱스 + Groq)
# ---------------------------------------------------------------------------
def _has_keys() -> bool:
    from dotenv import load_dotenv

    load_dotenv(common.rag.ENV_FILE)
    return bool(os.getenv("COHERE_API_KEY") and os.getenv("GROQ_API_KEY")) and (common.rag.VECTORDB_DIR / "chroma.sqlite3").exists()


integration = pytest.mark.skipif(not _has_keys(), reason="API 키 또는 vectordb/ 없음")


@integration
def test_integration_step3_cites_gold_chunk(monkeypatch):
    monkeypatch.undo()   # 가짜 검색기 대신 실제 인덱스
    rec = Step3().run("특허법 제42조 출원 방법")
    assert 28 in rec.retrieved and (28 in rec.sources or rec.answer.startswith(NOT_FOUND))
    assert not ev.ungrounded_articles(rec)
