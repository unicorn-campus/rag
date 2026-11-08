"""4단계: 외부 전송 도구에 사람 승인 (교재 36~37p Rule of Two, 42~43p 권한 오남용 대응).

3단계에 더하는 장치
1. 고위험 작업 사람 승인: send_email(외부 전송)은 실행 전에 그래프가 멈추고(interrupt) 사람의 승인·거부를 기다림.
   검색은 읽기 전용이라 승인 없이 실행 (HumanInTheLoopMiddleware, interrupt_on)
2. 이어 하기: 멈춘 지점을 체크포인터(InMemorySaver)와 thread_id로 기억했다가 Command(resume=...)로 이어 감(교재 19~20p)
3. 도구 허용 목록: 에이전트에 준 도구는 search_patent_law, send_email 두 개뿐(2단계 도구 계약 그대로)

허용 여부를 LLM에 묻지 않음. 모델이 "보냈다"고 말해도 승인 전에는 outbox에 아무것도 생기지 않음.
승인 대기 시간은 요청 시간 상한(1단계)에서 뺌.

확인(67p): 승인 전에는 실행되지 않고 대기.

실행:
    python step4_approval.py "특허권 존속기간을 요약해서 team@example.com으로 메일로 보내 줘"
"""

from __future__ import annotations

import json
import time

from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import common
from common import RunRecord
from step0_baseline import run_cli
from step3_verify import Step3

HIGH_RISK_TOOLS = {"send_email"}
MAX_APPROVAL_ROUNDS = 3


def console_approver(actions: list[dict]) -> list[dict]:
    """터미널에서 사람이 승인·거부를 고름. 액션마다 결정 하나(순서 유지)."""
    decisions = []
    for a in actions:
        print("\n[승인 요청] 외부 전송 도구를 실행하려고 합니다.")
        print(f"  도구: {a['name']}")
        print("  입력: " + json.dumps(a["args"], ensure_ascii=False, indent=2)[:1500])
        ok = input("  실행할까요? [y/N] ").strip().lower() in ("y", "yes", "예", "ㅇ")
        decisions.append({"type": "approve"} if ok else {"type": "reject", "message": "사용자가 전송을 거부함"})
    return decisions


class Step4(Step3):
    stage = 4
    title = "외부 전송 사람 승인"

    def __init__(self, llm=None, approver=None) -> None:
        self.approver = approver or console_approver
        super().__init__(llm)

    def middleware(self) -> list:
        hitl = HumanInTheLoopMiddleware(
            interrupt_on={"send_email": {"allowed_decisions": ["approve", "reject"]},
                          "search_patent_law": False},
            description_prefix="외부 전송은 사람 승인이 필요함")
        return super().middleware() + [hitl]

    def checkpointer(self):
        return InMemorySaver()   # 승인을 기다리는 동안 State를 보관(운영에서는 PostgresSaver 등)

    def invoke(self, question: str, cfg: dict, rec: RunRecord) -> dict:
        t = common.tracker()
        result = self.agent.invoke({"messages": [{"role": "user", "content": question}]}, cfg)
        for _ in range(MAX_APPROVAL_ROUNDS):
            interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
            if not interrupts:
                break
            rec.pending_approval = True
            actions = interrupts[0].value["action_requests"]
            # 승인 전 상태 확인: 아직 아무것도 나가지 않았어야 함
            t.event("approval_wait", tools=[a["name"] for a in actions], sent_before_decision=len(t.sent),
                    outbox_before_decision=len(common.outbox_files()))
            waited = time.time()
            decisions = self.approver(actions)
            t.started += time.time() - waited      # 사람 대기 시간은 요청 시간 상한에서 뺌
            t.event("approval", decisions=[d["type"] for d in decisions])
            result = self.agent.invoke(Command(resume={"decisions": decisions}), cfg)
        return result


if __name__ == "__main__":
    raise SystemExit(run_cli(Step4))
