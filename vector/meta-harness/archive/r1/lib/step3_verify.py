"""3단계: 답변 스키마 검증과 근거 대조 (교재 34~35p, 할루시네이션 전파 대응 / 50~51p 코드 검사 먼저).

2단계에 더하는 장치 (싼 것부터 차례로 거르는 관문)
1. 스키마 검증: 최종 답을 Answer(text, sources) 스키마로 바꿔 검증함. 답 마지막 줄 "근거: C073, C077"을
   코드가 sources로 읽음(v3: 청크 id를 숫자 대신 C+세 자리로 써 조문 번호와 혼동하지 않게 함). 거절이 아닌데 sources가 비면 스키마 단계에서 거부
   (create_agent의 response_format=ToolStrategy도 시험했으나, Groq gpt-oss가 강제 도구 호출(tool_choice=required)을
   자주 깨뜨려 400 오류가 나서 형식 줄 파싱으로 바꿈)
2. 근거 대조(코드): ① 인용한 청크가 이번 실행에서 실제로 검색된 청크인가 ② 답에 쓴 조문 번호가 인용 청크 원문에 있는가
3. 검증 실패 시 체인 차단: 고치는 법을 담은 오류로 1회 재생성 → 그래도 실패하면 답을 내보내지 않고
   "문서에서 찾지 못했습니다"로 바꿈(차단 사유는 기록)

모델이 스스로 낸 확신도는 통과 기준으로 쓰지 않음. 판정은 모두 코드가 함.

확인(67p): 근거 없는 답변이 다음 단계(사용자)로 넘어가지 않음.

실행:
    python step3_verify.py "특허권의 존속기간은?"
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError, model_validator

import common
from common import NOT_FOUND, RunRecord, UsageCallback, articles_in
from step0_baseline import run_cli
from step2_context import CORE_PROMPT, Step2, doc_id, wrap_doc

MAX_REGENERATE = 1

OUTPUT_RULE = """

[출력 형식] 답의 마지막 줄은 반드시 "근거: C073, C077" 형식(검색 결과의 doc id를 그대로 씀. 조문 번호 제N조와 다름).
하네스가 이 줄로 근거를 대조하므로, 근거로 쓰지 않은 청크는 넣지 않음. 거절할 때는 이 줄을 쓰지 않음"""
SOURCES_LINE = re.compile(r"근거\s*[:：]\s*(.*)$", re.MULTILINE)
DOC_ID_RE = re.compile(r"\bC\s*(\d{1,3})\b")   # "C073" → 73

REGEN_PROMPT = """아래 검색 자료만 근거로 질문에 다시 답하세요. 앞선 답은 검증에 실패했습니다.

[질문]
{question}

[앞선 답]
{previous}

[검증 오류와 고치는 법]
{problems}

[검색 자료]
{docs}

[출력] JSON 하나만 출력: {{"text": "답(근거 조문 번호 포함)", "sources": ["C073"처럼 근거 doc id]}}
자료에 근거가 없으면 {{"text": "{not_found}", "sources": []}}"""


class Answer(BaseModel):
    """최종 답변. 답을 다 정했을 때 한 번만 호출한다."""

    text: str = Field(min_length=1, max_length=2000, description="사용자에게 보여 줄 답. 근거 조문 번호(제N조)를 밝힘")
    sources: list[int] = Field(default_factory=list,
                               description="답의 근거가 된 청크 번호(<doc id> C073 → 73). 근거를 못 찾아 거절할 때만 빈 목록")

    @model_validator(mode="after")
    def need_sources(self):
        if NOT_FOUND not in self.text and not self.sources:
            raise ValueError("sources가 비었습니다. 근거 청크 번호를 넣거나, 근거가 없으면 text를 "
                             f"'{NOT_FOUND}'로 하세요.")
        return self


def to_answer(text: str) -> Answer:
    """자유 문장 답을 Answer 스키마로 바꿈. 마지막 '근거:' 줄의 C073 같은 doc id가 sources. 스키마 위반이면 ValidationError."""
    lines = SOURCES_LINE.findall(text or "")
    sources = [int(n) for n in DOC_ID_RE.findall(lines[-1])] if lines else []
    body = SOURCES_LINE.sub("", text or "").strip() if lines else (text or "").strip()
    return Answer(text=body or (text or "").strip() or "-", sources=sources)


def check_grounding(ans: Answer, retrieved: list[int], chunks: dict[int, str]) -> list[str]:
    """근거 대조. 문제 목록(비면 통과). 각 문제에 고치는 법을 함께 적음(교재 51p)."""
    if NOT_FOUND in ans.text:
        return []
    problems = []
    unknown = [s for s in ans.sources if s not in retrieved]
    if unknown:
        problems.append(f"오류: 검색하지 않은 청크 {[doc_id(u) for u in unknown]}를 인용함. "
                        "고치는 법: 검색 자료의 doc id(C073 형식)만 근거 줄에 쓸 것")
    cited_articles = set().union(*(articles_in(chunks.get(s, "")) for s in ans.sources if s in retrieved))
    missing = sorted(articles_in(ans.text) - cited_articles)
    if missing:
        problems.append(f"오류: {', '.join(missing)}는 인용한 청크 원문에 없음. "
                        "고치는 법: 그 조문이 있는 청크를 sources에 넣거나, 원문에 없는 조문 번호는 빼고 쓸 것")
    return problems


def parse_answer(text: str) -> Answer:
    """LLM 출력에서 바깥 {...}를 떼어 Answer로 검증함(Groq JSON 모드 대신 직접 파싱, 과거 실습 교훈).
    sources의 "C073" 같은 doc id는 청크 번호 73으로 바꿈. 숫자만 쓴 값은 조문 번호와 헷갈리므로 받지 않음."""
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    data["sources"] = [int(m) for s in data.get("sources", []) for m in DOC_ID_RE.findall(str(s))]
    return Answer.model_validate(data)


class Step3(Step2):
    stage = 3
    title = "답변 스키마·근거 대조"

    def system_prompt(self) -> str:
        return CORE_PROMPT + OUTPUT_RULE

    def regenerate(self, question: str, previous: str, problems: list[str], usage: UsageCallback) -> tuple[Answer | None, list[str]]:
        chunks = common.get_retrieval().chunks if common.tracker().retrieved else {}
        docs = "\n\n".join(wrap_doc(i, chunks[i]) for i in common.tracker().retrieved) or "(검색 자료 없음)"
        prompt = REGEN_PROMPT.format(question=question, previous=previous or "(없음)", problems="\n".join(problems),
                                     docs=docs, not_found=NOT_FOUND)
        try:
            return parse_answer(self.llm.invoke(prompt, config={"callbacks": self.callbacks(usage)}).content or ""), []
        except (ValidationError, json.JSONDecodeError, ValueError) as e:
            return None, [f"오류: 답 형식이 Answer 스키마와 다름({type(e).__name__}). 고치는 법: JSON 하나만 출력할 것"]

    def finish(self, question: str, state: dict | None, rec: RunRecord, usage: UsageCallback) -> None:
        super().finish(question, state, rec, usage)          # 1단계: 멈췄으면 부분 결과(자유 문장)
        t = common.tracker()
        chunks = common.get_retrieval().chunks if t.retrieved else {}
        try:   # ① 스키마 검증(코드)
            ans, problems = to_answer(rec.answer), []
        except ValidationError as e:
            ans, problems = None, [f"오류: {e.errors()[0]['msg']} 고치는 법: 마지막 줄에 '근거: C073'처럼 doc id를 쓸 것"]
        if ans:   # ② 근거 대조(코드)
            problems = check_grounding(ans, t.retrieved, chunks)
        for attempt in range(MAX_REGENERATE):
            if not problems:
                break
            t.event("verify_failed", attempt=attempt + 1, problems=problems)
            previous = ans.text if ans else rec.answer
            ans, problems = self.regenerate(question, previous, problems, usage)
            if ans:
                problems = check_grounding(ans, t.retrieved, chunks)
        if problems or ans is None:   # 체인 차단: 검증을 통과하지 못한 답은 내보내지 않음
            t.event("blocked_ungrounded", problems=problems)
            rec.answer, rec.sources = f"{NOT_FOUND} (근거 검증을 통과한 답이 없어 내보내지 않음)", []
            return
        rec.sources = ans.sources
        note = f"\n(일부 범위만 확인: {rec.stopped})" if rec.stopped and "일부 범위" not in ans.text else ""
        rec.answer = ans.text + note + (f"\n근거: {', '.join(doc_id(s) for s in ans.sources)}" if ans.sources else "")


if __name__ == "__main__":
    raise SystemExit(run_cli(Step3))
