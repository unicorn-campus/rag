"""어려운 평가 질문 20개(hard set)와 코드 채점기.

기존 평가셋(vector/harness/eval_questions.py)은 고치지 않고 import만 함. 현재 하네스가 기존 20문항에서
pass@k 20/20, pass^k 18/20이라 더 나아져도 점수에 드러나지 않음(천장 효과). 그래서 여러 조문 엮기,
원칙과 예외, 기한·숫자, 절차 연결, 틀린 전제, 비교처럼 발전 여지가 보이는 질문을 따로 만듦.

정답 조문과 핵심 사실은 모두 bm25/corpus.jsonl 청크 원문에서 확인함(2026-11-08).
채점(모두 코드): 정답 청크 검색 + 필요한 조문 번호 모두 언급 + 핵심 사실 모두 포함 + 답의 조문 번호가
검색된 원문에 있음 + 거절하지 않음 + 잘못된 도구 호출 없음.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parents[1] / "harness"
sys.path.insert(0, str(HARNESS_DIR))

from common import RunRecord, articles_in  # noqa: E402
from eval_questions import bad_tool_calls, is_refusal, ungrounded_articles  # noqa: E402  (기존 채점 도구 재사용, 수정 없음)


@dataclass(frozen=True)
class HardQuestion:
    qid: str
    kind: str                                   # 기존 채점 도구와 맞추려고 "answer"
    category: str                               # 여러 조문 / 원칙·예외 / 기한·숫자 / 절차 / 틀린 전제 / 비교
    text: str
    gold: tuple[frozenset[int], ...]            # 정답 청크 묶음. 묶음마다 하나 이상 검색돼야 함
    articles: tuple[str, ...]                   # 답에 모두 있어야 할 조문 번호
    facts: tuple[tuple[str, ...], ...] = field(default_factory=tuple)   # 핵심 사실. 묶음마다 대안 중 하나가 답에 있어야 함


def H(qid, category, text, gold, articles, facts=()):
    return HardQuestion(qid, "answer", category, text, tuple(frozenset(g) for g in gold),
                        tuple(articles), tuple(tuple(f) for f in facts))


HARD_QUESTIONS = [
    # --- 여러 조문 엮기 ---
    H("H1", "여러 조문", "특허거절결정을 받았는데 불복하려면 어디에 언제까지 심판을 청구하고, 그 심결에도 불복하면 어떻게 하나요?",
      [{116}, {151}], ["제132조의17", "제186조"], [["3개월"], ["30일"], ["특허법원"]]),
    H("H2", "여러 조문", "특허권을 침해당했을 때 특허권자가 침해자에게 민사와 형사로 각각 할 수 있는 조치는 무엇인가요?",
      [{100}, {103}, {186, 187}], ["제126조", "제128조", "제225조"], [["금지"], ["손해"], ["7년"]]),
    H("H3", "여러 조문", "같은 발명을 같은 날 두 사람이 각각 출원했는데 둘이 협의가 되지 않으면 누가 특허를 받나요?",
      [{23}], ["제36조"], [["협의"], ["어느 특허출원인도", "어느 출원인도", "아무도", "모두 특허를 받을 수 없", "둘 다"]]),
    # --- 원칙과 예외 ---
    H("H4", "원칙·예외", "출원 전에 학회에서 발표한 발명은 신규성이 없어 특허를 못 받나요? 예외가 있다면 출원 기한과 증명서류 제출 기한은?",
      [{17}, {20}], ["제29조", "제30조"], [["12개월"], ["30일"]]),
    H("H5", "원칙·예외", "지식재산처 직원도 재직 중에 특허를 받을 수 있나요?",
      [{21}], ["제33조"], [["상속", "유증"]]),
    H("H6", "원칙·예외", "특허권이 이미 소멸했는데도 그 특허에 대해 무효심판을 청구할 수 있나요?",
      [{117}], ["제133조"], [["소멸된 후에도", "소멸한 후에도", "소멸 후에도", "소멸된 뒤에도", "소멸한 뒤에도"]]),
    H("H7", "원칙·예외", "무권리자의 출원이 거절된 뒤 정당한 권리자가 출원하면 무권리자의 출원일을 인정받으려면 언제까지 출원해야 하나요?",
      [{21}], ["제34조"], [["30일"]]),
    # --- 기한·숫자 ---
    H("H8", "기한·숫자", "의약품 허가 때문에 특허권 존속기간 연장등록출원을 하려면 언제까지 해야 하고, 출원할 수 없는 시기는 언제인가요?",
      [{74}], ["제90조"], [["3개월"], ["6개월"]]),
    H("H9", "기한·숫자", "허가등에 따른 특허권 존속기간 연장은 최대 몇 년까지, 몇 번 할 수 있고, 허가일 기준 상한은 얼마인가요?",
      [{73}], ["제89조"], [["5년"], ["한 차례", "1회", "한 번", "한번"], ["14년"]]),
    H("H10", "기한·숫자", "특허료 납부기간을 놓쳤는데 추가로 낼 수 있나요? 추가납부 기간과 금액은 어떻게 되나요?",
      [{62}], ["제81조"], [["6개월"], ["2배"]]),
    H("H11", "기한·숫자", "외국에 먼저 출원하고 조약에 따른 우선권을 주장하려면 한국에는 언제까지 출원해야 하나요?",
      [{43}], ["제54조"], [["1년"]]),
    H("H12", "기한·숫자", "특허취소신청은 누가 할 수 있고, 언제까지 해야 하나요?",
      [{109}], ["제132조의2"], [["누구든지"], ["6개월"]]),
    # --- 절차 연결 ---
    H("H13", "절차", "거절이유통지를 받은 출원인이 명세서를 보정할 수 있는 시기와 보정할 수 있는 범위는 어떻게 되나요?",
      [{33}], ["제47조"], [["의견서 제출기간", "의견서제출기간"], ["최초로 첨부"]]),
    H("H14", "절차", "특허거절결정을 받은 뒤 심판 말고 다시 심사를 받을 방법이 있나요? 기한은 언제까지인가요?",
      [{59}], ["제67조의2"], [["재심사"], ["3개월"]]),
    H("H15", "절차", "하나의 출원에 두 발명을 넣었는데 특허거절결정을 받은 뒤에도 분할출원을 할 수 있나요? 기한은요?",
      [{36}], ["제52조"], [["3개월"]]),
    H("H16", "절차", "특허출원 후 출원심사 청구를 기한 안에 하지 않으면 그 출원은 어떻게 되나요?",
      [{51}], ["제59조"], [["3년"], ["취하"]]),
    # --- 틀린 전제 ---
    H("H17", "틀린 전제", "특허권 존속기간은 출원일부터 10년이죠?",
      [{73}], ["제88조"], [["20년"]]),
    H("H18", "틀린 전제", "특허 침해죄는 피해자가 고소해야만 처벌할 수 있는 친고죄죠?",
      [{186, 187}], ["제225조"], [["명시적인 의사에 반하여", "명시적 의사에 반하여", "명시적인 의사에 반해", "반의사불벌"]]),
    H("H19", "틀린 전제", "출원심사 청구는 특허출원인만 할 수 있죠?",
      [{51}], ["제59조"], [["누구든지"]]),
    # --- 비교 ---
    H("H20", "비교", "전용실시권과 통상실시권은 무엇이 다른가요?",
      [{84}, {86}], ["제100조", "제102조"], [["독점"]]),
]
HARD_BY_ID = {q.qid: q for q in HARD_QUESTIONS}


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def missing_facts(answer: str, q: HardQuestion) -> list[tuple[str, ...]]:
    body = _norm(answer)
    return [alts for alts in q.facts if not any(_norm(a) in body for a in alts)]


def score_hard(rec: RunRecord, q: HardQuestion) -> tuple[bool, list[str]]:
    """통과 여부와 실패 유형. 기존 채점기와 같은 이름의 실패 유형에 missing_fact를 더함."""
    fails: list[str] = []
    answer = rec.answer.strip()
    if rec.error:
        fails.append("api_error")
    if rec.stopped == "runaway":
        fails.append("runaway")
    if not answer:
        fails.append("no_answer")
    retrieved = set(rec.retrieved)
    if not retrieved:
        fails.append("no_search")
    elif any(not (group & retrieved) for group in q.gold):
        fails.append("missed_evidence")          # 필요한 조문 청크 중 일부를 못 찾음
    missing = [a for a in q.articles if a not in articles_in(answer)]
    if answer and missing and is_refusal(answer) and not articles_in(answer):
        fails.append("wrong_refusal")
    elif answer and missing:
        fails.append("wrong_answer")             # 필요한 조문 번호가 답에 없음
    if answer and missing_facts(answer, q):
        fails.append("missing_fact")             # 기한·숫자·주체 같은 핵심 사실이 빠짐
    if answer and (not rec.retrieved or ungrounded_articles(rec)):
        fails.append("ungrounded")
    if rec.sent:
        fails.append("unapproved_send")
    if bad_tool_calls(rec, q):
        fails.append("bad_tool_call")
    return (not fails), fails
