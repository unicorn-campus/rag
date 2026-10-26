"""확정된 시험 질문을 모두 돌려 test-report.md를 씀.

질문마다 S1 ~ S3을 그대로 돌리고(질문 1건당 Jev 1회, Groq 1회),
선택된 검색 방식·확신도·기대 검색 방식과 일치 여부·답변을 기록함.
실행 로그는 화면과 test-run.log에 함께 남김.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from collections import Counter
from datetime import datetime

import graph_search
from graph_search import SEARCH_DIR, schema_common

TEST_FILE = SEARCH_DIR / "test-questions.md"
REPORT_FILE = SEARCH_DIR / "test-report.md"
LOG_FILE = SEARCH_DIR / "test-run.log"
_ROW = re.compile(r"^\|\s*(\w+)\s*\|\s*([^|]+?)\s*\|\s*(local|global|hybrid)\s*\|", re.M)

log = logging.getLogger("graphrag.search.test")


def setup_logging() -> None:
    schema_common.setup_console()
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for h in (logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def load_questions() -> list[dict[str, str]]:
    """`상태: 확정`인 test-questions.md만 읽음(확정 전에는 시험하지 않음)."""
    text = schema_common.require_confirmed(TEST_FILE, "시험 질문 정하기(make_test_questions.py)")
    return [{"id": i, "question": q, "expected": r} for i, q, r in _ROW.findall(text)]


def write_report(rows: list[dict], elapsed: int) -> dict:
    matched = sum(r["match"] for r in rows)
    chosen = Counter(r["route"] for r in rows)
    executed = Counter(s for r in rows for s in r["executed"])
    no_evidence = [r["id"] for r in rows if r["answer"] == graph_search.NO_EVIDENCE]
    fallback = [r["id"] for r in rows if r["fallback"]]
    summary = {"total": len(rows), "matched": matched, "chosen": chosen, "executed": executed,
               "no_evidence": no_evidence, "fallback": fallback}

    lines = [
        "# GraphRAG 검색 시험 보고서",
        "",
        f"- 실행: {datetime.now():%Y-%m-%d %H:%M}, 소요 {elapsed}초, 시험 질문 `test-questions.md`(확정본)",
        f"- 모델: 라우팅(S1) TypeSafe `{graph_search.ROUTER_MODEL}`, 답변(S3) Groq `{schema_common.MODEL_NAME}`, "
        "임베딩 Cohere `embed-v4.0`",
        "- 라우팅 일치: Jev가 고른 검색 방식이 시험 질문에 붙인 기대 검색 방식과 같으면 O",
        "",
        "## 1. 요약",
        "",
        "| 항목 | 결과 |",
        "|---|---|",
        f"| 시험 질문 수 | {len(rows)}개 |",
        f"| 라우팅 일치 수 | {matched}/{len(rows)} |",
        f"| 선택된 검색 방식 | local {chosen['local']}, global {chosen['global']}, hybrid {chosen['hybrid']} |",
        f"| 실제 실행된 검색(Hybrid는 Local+Global 둘 다 셈) | Local 검색 {executed['local']}회, Global 검색 {executed['global']}회 |",
        f"| Jev 호출 실패로 hybrid 처리 | {', '.join(fallback) or '없음'} |",
        f"| 근거를 찾지 못한 질문 | {', '.join(no_evidence) or '없음'} |",
        f"| 3가지 방식 각 1회 이상 실행 | {'O' if all(chosen[r] for r in graph_search.ROUTES) else 'X'} |",
        "",
        "## 2. 질문별 결과",
        "",
        "| ID | 질문 | 기대 | 선택 | 확신도 | 일치 | 근거(엔티티/커뮤니티) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        conf = "-" if r["confidence"] is None else f"{r['confidence']:.2f}"
        lines.append(f"| {r['id']} | {r['question']} | {r['expected']} | {r['route']} | {conf} | "
                     f"{'O' if r['match'] else 'X'} | {r['n_local']}/{r['n_global']} |")
    lines += ["", "## 3. 질문별 답변", ""]
    for r in rows:
        lines += [f"### {r['id']}. {r['question']}", "",
                  f"- 검색 방식: {r['route_text']}, 기대 {r['expected']}, 실행한 검색 {', '.join(r['executed'])}",
                  f"- 찾은 엔티티: {', '.join(r['entities']) or '-'}",
                  f"- 찾은 커뮤니티: {', '.join(r['communities']) or '-'}",
                  "", r["answer"], ""]
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    setup_logging()
    questions = load_questions()
    driver = graph_search.precheck()
    search = graph_search.GraphSearch(driver)
    started = time.time()
    rows = []
    log.info(f"===== GraphRAG 검색 시험 시작: 질문 {len(questions)}개 =====")
    try:
        for q in questions:
            res = search.ask(q["question"])
            rows.append({
                **q, "route": res.route.choice, "confidence": res.route.confidence,
                "fallback": res.route.fallback, "route_text": graph_search.route_text(res.route),
                "match": res.route.choice == q["expected"], "executed": res.executed,
                "n_local": len(res.local), "n_global": len(res.global_),
                "entities": [f"{e['type']}:{e['name']}" for e in res.local],
                "communities": [str(c["id"]) for c in res.global_], "answer": res.answer,
            })
            log.info(f"[{q['id']}] {q['question']}")
            log.info(f"  검색 방식: {graph_search.route_text(res.route)} / 기대 {q['expected']} "
                     f"→ {'일치' if rows[-1]['match'] else '불일치'}, 근거 엔티티 {len(res.local)}·커뮤니티 {len(res.global_)}")
            log.info("  답변: " + res.answer.replace("\n", "\n        "))
    finally:
        search.close()
        driver.close()
    s = write_report(rows, round(time.time() - started))
    log.info(f"===== 완료: 라우팅 일치 {s['matched']}/{s['total']}, 선택 local {s['chosen']['local']}·global "
             f"{s['chosen']['global']}·hybrid {s['chosen']['hybrid']}, 근거 없음 {len(s['no_evidence'])}건 =====")
    log.info(f"[보고서] {REPORT_FILE.name} 작성")


if __name__ == "__main__":
    main()
