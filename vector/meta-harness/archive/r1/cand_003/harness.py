"""cand_003: cand_000 + find_article(조문 번호로 찾기) + 답 작성 뒤 검토 단계(review pass).

검토 단계: 복합 질문이면 코드가 한 번 더 LLM을 불러, 이미 검색된 모든 청크와 초안을 대조해
빠진 주체·기한·숫자·예외·다음 단계를 보강한다. 결과는 근거 대조를 다시 통과해야 채택하고,
실패하면 초안을 그대로 둔다. send_email·승인·상한·트레이스는 상속(Step5) 그대로.
"""

import json
import re

from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, Field, ValidationError

import common
from common import NOT_FOUND
from step2_context import contract_tools, doc_id, wrap_doc
from step3_verify import check_grounding, parse_answer
from step5_trace import Step5

MAX_REVIEW_CHUNKS = 6
REVIEW_CHUNK_CHARS = 1400
MAX_ARTICLE_CHUNKS = 3

HEAD_RE = re.compile(r"제\s*\d+\s*조(?:\s*의\s*\d+)?\s*\(")
CHAPTER_RE = re.compile(r"제\s*\d+\s*[장절편]")
ARTICLE_ARG_RE = re.compile(r"(\d{1,3})\s*조?\s*(?:의\s*(\d{1,3}))?")
COMPLEX_MARKERS = ("및", "와 ", "과 ", "그리고", "또는", "각각", "언제", "어떻게", "기한", "기간", "범위", "차이",
                   "비교", "경우", "이후", "다음", "대신", "말고", "예외", "원칙", "절차", "불복", "가능", "아닌가")

EXTRA_RULES = """

[복합 질문 규칙]
- 질문 요소(주체·기한·숫자, 원칙과 예외, 절차의 다음 단계, 두 제도 비교, 틀렸을 수 있는 전제)마다 근거 청크를 확인하고 답함
- 질문의 전제가 원문과 다르면 먼저 바로잡음. 항·호 번호는 원문 그대로 씀
- 요소가 여럿이면 7문장 이내로 답해도 됨
[절차 지도] 어느 조문을 이어서 볼지 정하는 힌트일 뿐이며 find_article이나 검색 원문으로 확인한 뒤에만 인용함
- 거절결정 이후: 재심사 청구 또는 거절결정 불복 심판, 심결에 대한 불복은 법원 소송(재심과 구별)
- 보정: 시기와 범위가 항·호별로 다름 / 특허료: 납부, 추가납부, 이후 회복 / 존속기간: 기간, 연장, 연장등록출원
- 후속 단계 조문이 검색 결과에 없으면 find_article(article='제OO조')처럼 번호로 직접 찾음"""

REVIEW_PROMPT = """특허법 Q&A 답변 검토자입니다. 아래 [검색 자료]만 근거로, [초안]이 질문의 모든 요소를 채웠는지 점검하고 보강한 최종 답을 쓰세요.

[점검 순서]
1. 질문이 묻는 요소를 속으로 나열(주체, 기한·숫자, 원칙과 예외, 절차의 다음 단계, 비교 대상, 질문의 틀린 전제)
2. 각 요소가 초안에 있는지, 검색 자료의 항·호 단위 사실과 맞는지 확인
3. 빠진 요소는 자료에서 찾아 추가. 자료에 없는 사실·조문 번호는 절대 쓰지 않음. 초안의 맞는 내용은 유지
4. 7문장 이내, 기한·숫자·항 번호는 원문 그대로

[질문]
{question}

[초안]
{draft}

[검색 자료]
{docs}

[출력] JSON 하나만 출력: {{"text": "최종 답(근거 조문 번호 포함)", "sources": ["C073"처럼 실제 근거로 쓴 doc id]}}"""


class ArticleInput(BaseModel):
    article: str = Field(min_length=1, max_length=20, description="조문 번호. 예: '제186조', '186', '제132조의17'")


def parse_article(arg: str):
    m = ARTICLE_ARG_RE.search(arg or "")
    return (m.group(1), m.group(2) or "") if m else None


def is_continuation(text: str) -> bool:
    head = (text or "").lstrip()[:80]
    return not (HEAD_RE.search(head) or CHAPTER_RE.match(head))


def looks_complex(question: str) -> bool:
    q = question or ""
    hits = sum(1 for m in COMPLEX_MARKERS if m in q)
    return hits >= 2 or len(q) >= 45


class Candidate(Step5):
    def __init__(self, llm=None, approver=None, trace_file=None) -> None:
        super().__init__(llm, approver, trace_file)

    def system_prompt(self) -> str:
        return super().system_prompt() + EXTRA_RULES

    def tools(self) -> list:
        base = list(contract_tools())   # send_email(허용 도메인 검사 포함)과 search_patent_law 그대로

        @tool("find_article", args_schema=ArticleInput)
        def find_article(article: str) -> str:
            """조문 번호로 특허법 조문 원문 청크를 직접 찾는다(읽기 전용). 검색어로 잘 안 잡히는 후속 단계 조문이나
            이미 번호를 아는 조문을 확인할 때 쓴다. 예: article='제186조', article='제132조의17'.
            조문이 길어 다음 청크로 이어지면 이어지는 청크도 함께 준다."""
            parsed = parse_article(article)
            t = common.tracker()
            if not parsed:
                t.tool("find_article", {"article": article}, ok=False, note="번호 해석 실패")
                raise ToolException("조문 번호를 읽지 못했습니다. 예: '제186조', '제132조의17' 형식으로 재호출하세요.")
            num, sub = parsed
            suffix = rf"\s*의\s*{sub}" if sub else r"(?!\s*의)"
            pat = re.compile(rf"제\s*{num}\s*조{suffix}\s*\(")
            chunks = common.get_retrieval().chunks
            hits = [i for i in sorted(chunks) if pat.search(chunks[i][:120])]
            if not hits:
                hits = [i for i in sorted(chunks) if pat.search(chunks[i])][:2]
            out: list[int] = []
            for i in hits[:2]:
                if i not in out:
                    out.append(i)
                nxt = i + 1
                if nxt in chunks and nxt not in hits and is_continuation(chunks[nxt]) and len(out) < MAX_ARTICLE_CHUNKS:
                    out.append(nxt)
            out = out[:MAX_ARTICLE_CHUNKS]
            t.tool("find_article", {"article": article}, ok=bool(out), note="" if out else "없음")
            if not out:
                raise ToolException(f"'{article}' 조문 청크를 찾지 못했습니다. 번호를 확인하거나 search_patent_law로 핵심어를 검색하세요.")
            for i in out:
                if i not in t.retrieved:
                    t.retrieved.append(i)
            return "\n\n".join(wrap_doc(i, chunks[i]) for i in out)

        find_article.handle_tool_error = True
        find_article.handle_validation_error = lambda e: "입력 오류. article은 '제186조' 같은 조문 번호입니다. 고쳐서 재호출하세요."
        return base + [find_article]

    def finish(self, question, state, rec, usage) -> None:
        super().finish(question, state, rec, usage)   # 검증·차단·부분 결과 처리
        t = common.tracker()
        draft = rec.answer or ""
        if (not t.retrieved or not rec.sources or NOT_FOUND in draft or rec.stopped
                or not looks_complex(question)):
            return
        try:
            self.review(question, rec, usage)
        except Exception:   # 검토 실패는 초안 유지
            return

    def review(self, question, rec, usage) -> None:
        t = common.tracker()
        chunks = common.get_retrieval().chunks
        ids = [i for i in t.retrieved if i in chunks][:MAX_REVIEW_CHUNKS]
        docs = "\n\n".join(wrap_doc(i, chunks[i][:REVIEW_CHUNK_CHARS]) for i in ids)
        draft = re.sub(r"\n?근거\s*[:：].*$", "", rec.answer, flags=re.MULTILINE).strip()
        prompt = REVIEW_PROMPT.format(question=question, draft=draft, docs=docs)
        raw = self.llm.invoke(prompt, config={"callbacks": self.callbacks(usage)}).content or ""
        try:
            new = parse_answer(raw)
        except (ValidationError, json.JSONDecodeError, ValueError):
            return
        if NOT_FOUND in new.text or not new.sources:
            return
        if check_grounding(new, t.retrieved, chunks):
            return
        rec.sources = new.sources
        rec.answer = new.text + "\n근거: " + ", ".join(doc_id(s) for s in new.sources)
        try:
            t.event("review_pass", changed=True, chunks=len(ids))
        except Exception:
            pass
