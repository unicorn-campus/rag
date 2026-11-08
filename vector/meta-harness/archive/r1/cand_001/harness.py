"""cand_001: 복합 질문용 다중 검색어 도구 + 인접 청크 펼치기 도구 + 분해 점검 프롬프트.

부모 cand_000(Step5 그대로)에서 바꾼 것
1. search_patent_law: query에 '|'로 구분한 검색어를 최대 3개까지 한 번에 검색(모델 호출 수 증가 없이 재현율 향상)
2. read_nearby_chunks: 이미 찾은 청크의 앞뒤 청크를 펼치는 읽기 전용 도구(절차 연결·예외 단서 확인)
3. 시스템 프롬프트에 하위 질문 분해와 답 작성 전 점검 규칙 추가
send_email 도구·승인·상한·트레이스는 그대로(상속).
"""

import re

from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, Field

import common
from step2_context import contract_tools, wrap_doc
from step5_trace import Step5

MAX_SUBQUERIES = 3
PER_QUERY_WHEN_MULTI = 3     # 검색어가 여럿이면 검색어당 상위 몇 개를 보여 줄지
MAX_DOCS = 8                 # 한 번의 검색 결과에 담는 청크 상한(입력 토큰 억제)

EXTRA_RULES = """

[복합 질문 규칙]
- 질문에 요소가 둘 이상(여러 주체·기한·숫자, 원칙과 예외, 절차의 다음 단계, 두 제도 비교, 틀렸을 수 있는 전제)이면
  먼저 하위 질문으로 나눔. 첫 검색에서 하위 질문별 검색어를 '|'로 묶어 한 번에 검색함(최대 3개).
  예: query='특허권 이전 등록 효력 | 이전 신고 기한 | 공유 지분 양도 동의'
- 찾은 조문이 절차의 중간이거나 단서·예외 문장이 잘려 있으면 read_nearby_chunks(chunk_id='C073')로 앞뒤 청크를 확인함
- 답을 쓰기 전에 하위 질문마다 근거 청크가 있는지 점검함. 없는 부분은 한 번 더 검색하고, 그래도 없으면 확인하지 못한 부분임을 밝힘
- 기한·숫자·주체·항 번호(제N조제M항)는 검색 원문에 적힌 그대로 씀. 질문의 전제가 원문과 다르면 먼저 바로잡음
- 질문에 요소가 여럿이면 7문장 이내로 답해도 됨"""


class MultiSearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=160,
                       description="핵심어. 요소가 여럿이면 하위 질문별 검색어를 '|'로 구분(최대 3개). "
                                   "예: '존속기간 연장 요건 | 연장 출원 기한'")


class NearbyInput(BaseModel):
    chunk_id: str = Field(pattern=r"^C\d{3}$", description="이미 검색된 청크 id. 예: 'C073'")


def split_queries(query: str) -> list[str]:
    seen, out = set(), []
    for part in re.split(r"[|\n]", query or ""):
        q = part.strip()[:60]
        if len(q) >= 2 and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:MAX_SUBQUERIES]


class Candidate(Step5):
    def __init__(self, llm=None, approver=None, trace_file=None) -> None:
        super().__init__(llm, approver, trace_file)

    def system_prompt(self) -> str:
        return super().system_prompt() + EXTRA_RULES

    def tools(self) -> list:
        base = contract_tools()
        mail = next(t for t in base if t.name == "send_email")   # 허용 도메인 검사가 든 원본 그대로

        @tool("search_patent_law", args_schema=MultiSearchInput)
        def search_patent_law(query: str) -> str:
            """대한민국 특허법 조문을 검색한다. 질문에 답하기 전에 먼저 호출한다.
            요소가 여럿인 질문(원칙과 예외, 절차의 다음 단계, 기한과 주체, 비교)은 하위 질문별 검색어를
            '|'로 묶어 한 번에 넣는다(최대 3개). 예: query='특허권 존속기간 | 존속기간 연장 | 연장 출원 기한'.
            결과는 <doc id="C073"> 형식의 청크다. 특허법 외 법률(저작권법·상표법)은 없다."""
            queries = split_queries(query)
            if not queries:
                raise ToolException("검색어가 비었습니다. 질문의 핵심어(2자 이상)로 재호출하세요.")
            shown: list = []
            seen: set = set()
            for q in queries:
                docs = common.record_search(q)
                limit = len(docs) if len(queries) == 1 else PER_QUERY_WHEN_MULTI
                for d in docs[:limit]:
                    idx = d.metadata["chunk_index"]
                    if idx not in seen and len(shown) < MAX_DOCS:
                        seen.add(idx)
                        shown.append(d)
            if not shown:
                raise ToolException(f"'{query}'로 찾은 조문이 없습니다. 법률 용어나 조문 번호로 바꿔 재호출하세요.")
            return "\n\n".join(wrap_doc(d.metadata["chunk_index"], d.page_content) for d in shown)

        @tool("read_nearby_chunks", args_schema=NearbyInput)
        def read_nearby_chunks(chunk_id: str) -> str:
            """이미 검색된 청크의 바로 앞뒤 청크 원문을 보여 준다(읽기 전용).
            찾은 조문이 절차의 중간이거나 단서·예외·다음 단계가 이어지는 것 같을 때 쓴다. 예: chunk_id='C132'.
            새 주제를 찾는 용도가 아니다(그때는 search_patent_law)."""
            idx = int(chunk_id[1:])
            chunks = common.get_retrieval().chunks
            if idx not in chunks:
                raise ToolException(f"{chunk_id}는 없는 청크입니다. 검색 결과의 doc id를 그대로 쓰세요.")
            t = common.tracker()
            out = []
            for j in (idx - 1, idx + 1):
                if j in chunks:
                    if j not in t.retrieved:
                        t.retrieved.append(j)
                    out.append(wrap_doc(j, chunks[j]))
            t.tool("read_nearby", {"chunk_id": chunk_id}, ok=bool(out), note="" if out else "이웃 없음")
            if not out:
                raise ToolException(f"{chunk_id}의 앞뒤 청크가 없습니다.")
            return "\n\n".join(out)

        for t_ in (search_patent_law, read_nearby_chunks):
            t_.handle_tool_error = True
            t_.handle_validation_error = lambda e: (
                "입력 오류. search_patent_law의 query는 2~160자 핵심어('|'로 최대 3개), "
                "read_nearby_chunks의 chunk_id는 'C073' 형식입니다. 고쳐서 재호출하세요.")
        return [search_patent_law, read_nearby_chunks, mail]
