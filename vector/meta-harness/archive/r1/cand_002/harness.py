"""cand_002: 검색 결과에 이어지는 청크 자동 첨부 + 조문 번호로 찾는 find_article + 절차 지도 프롬프트.

부모 cand_000(Step5 그대로)에서 바꾼 것
1. search_patent_law: 상위 청크의 다음 청크가 같은 조문의 계속이면 자동으로 붙임(최대 2개, 코드가 강제)
2. find_article: 조문 번호(제186조, 제132조의17 등)로 조문 청크를 직접 찾는 읽기 전용 도구
3. 시스템 프롬프트: 복합 질문 점검 규칙과 절차 지도(힌트, 원문 확인 후에만 인용)
send_email 도구·승인·상한·트레이스·근거 대조는 그대로(상속).
"""

import re

from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, Field

import common
from step2_context import contract_tools, wrap_doc
from step5_trace import Step5

MAX_CONTINUATION = 2      # 검색 결과에 자동으로 붙이는 이어지는 청크 상한
CONT_FROM_TOP = 2         # 상위 몇 개 청크까지 이어지는 청크를 확인할지
MAX_ARTICLE_CHUNKS = 3    # find_article 결과 청크 상한

HEAD_RE = re.compile(r"제\s*\d+\s*조(?:\s*의\s*\d+)?\s*\(")
CHAPTER_RE = re.compile(r"제\s*\d+\s*[장절편]")
ARTICLE_ARG_RE = re.compile(r"(\d{1,3})\s*조?\s*(?:의\s*(\d{1,3}))?")

EXTRA_RULES = """

[복합 질문 규칙]
- 질문에 요소가 둘 이상(여러 주체·기한·숫자, 원칙과 예외, 절차의 다음 단계, 두 제도 비교, 틀렸을 수 있는 전제)이면
  요소마다 근거 청크가 있는지 확인한 뒤 답함. 하나라도 근거가 없으면 find_article이나 다른 검색어로 한 번 더 찾음
- 절차 질문(어디에·언제까지·그 다음엔)은 단계마다 주체·기한·방법을 빠뜨리지 않음. 한 조문이 항·호로 길면 항 번호까지 원문 그대로 씀
- 질문의 전제(숫자·주체·요건)가 원문과 다르면 먼저 바로잡음
- 요소가 여럿인 질문은 7문장 이내로 답해도 됨

[절차 지도] 아래는 어느 조문을 이어서 볼지 정하는 힌트일 뿐임. 번호를 그대로 믿지 말고 find_article로 원문을 확인한 뒤에만 인용함
- 심사 중 거절결정 이후: 재심사 청구(제67조의2) 또는 거절결정 불복 심판(제132조의17)
- 심판의 심결·결정에 대한 불복: 법원 소송(제186조), 재심(제178조 이하)과 구별함
- 명세서·도면 보정: 제47조(항·호별로 시기와 범위가 다름)
- 특허료: 납부(제79조), 추가납부(제81조), 이후 회복(제81조의3)
- 존속기간: 제88조, 연장 제89조, 연장등록출원 제90조
- 도구 사용: 후속 단계 조문이 검색 결과에 없으면 find_article(article='제186조')처럼 번호로 직접 찾음"""


class ArticleInput(BaseModel):
    article: str = Field(min_length=1, max_length=20,
                         description="조문 번호. 예: '제186조', '186', '제132조의17'")


def parse_article(arg: str) -> tuple[str, str] | None:
    m = ARTICLE_ARG_RE.search(arg or "")
    return (m.group(1), m.group(2) or "") if m else None


def is_continuation(text: str) -> bool:
    """새 조문(제N조( )이나 장 제목으로 시작하지 않으면 앞 조문의 계속으로 봄."""
    head = (text or "").lstrip()[:80]
    return not (HEAD_RE.search(head) or CHAPTER_RE.match(head))


def record(idx: int) -> None:
    t = common.tracker()
    if idx not in t.retrieved:
        t.retrieved.append(idx)


class Candidate(Step5):
    def __init__(self, llm=None, approver=None, trace_file=None) -> None:
        super().__init__(llm, approver, trace_file)

    def system_prompt(self) -> str:
        return super().system_prompt() + EXTRA_RULES

    def tools(self) -> list:
        base = contract_tools()
        mail = next(t for t in base if t.name == "send_email")        # 허용 도메인 검사가 든 원본 그대로
        orig_search = next(t for t in base if t.name == "search_patent_law")

        @tool("search_patent_law", args_schema=orig_search.args_schema)
        def search_patent_law(query: str) -> str:
            """대한민국 특허법 조문을 검색한다. 질문에 답하기 전에 먼저 호출한다.
            예: query='특허권 존속기간', query='제42조 특허출원'. 관련도 상위 청크를 <doc id="C073"> 형식으로 돌려주고,
            조문이 다음 청크로 이어지면 이어지는 청크도 함께 붙인다. 한 번에 한 주제만 찾는다.
            조문 번호를 이미 알면 find_article이 더 정확하다. 특허법 외 법률(저작권법·상표법)은 없다."""
            query = query.strip()
            if len(query) < 2:
                raise ToolException("검색어가 비었습니다. 질문의 핵심어(2~100자)로 재호출하세요.")
            docs = common.record_search(query)
            if not docs:
                raise ToolException(f"'{query}'로 찾은 조문이 없습니다. 법률 용어나 조문 번호로 바꿔 재호출하세요.")
            chunks = common.get_retrieval().chunks
            shown = [d.metadata["chunk_index"] for d in docs]
            extra: list[int] = []
            for idx in shown[:CONT_FROM_TOP]:
                nxt = idx + 1
                if (len(extra) < MAX_CONTINUATION and nxt in chunks and nxt not in shown
                        and nxt not in extra and is_continuation(chunks[nxt])):
                    extra.append(nxt)
            parts = [wrap_doc(d.metadata["chunk_index"], d.page_content) for d in docs]
            for j in extra:
                record(j)
                parts.append(wrap_doc(j, chunks[j]))
            return "\n\n".join(parts)

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
                record(i)
            return "\n\n".join(wrap_doc(i, chunks[i]) for i in out)

        for t_ in (search_patent_law, find_article):
            t_.handle_tool_error = True
            t_.handle_validation_error = lambda e: (
                "입력 오류. search_patent_law의 query는 2~100자 핵심어, "
                "find_article의 article은 '제186조' 같은 조문 번호입니다. 고쳐서 재호출하세요.")
        return [search_patent_law, find_article, mail]
