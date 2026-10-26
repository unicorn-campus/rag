"""검증용(holdout) 기대 결과 초안 만들기(LLM 초안 → 사람 확정).

개발용 시험 청크 10건에 맞춰 방안을 고르면 과적합되므로, 스키마 설계·채우기 시험에 한 번도 쓰지 않은
청크 6건으로 최적안을 따로 확인함. 초안 작성은 원래 6단계(step6_expected.py)의 프롬프트와 검사 함수를 그대로 씀(읽기만 함).

출력: holdout-expected.json(status: 초안), holdout-expected.md(사람 검토용)

실행(저장소 루트에서):
    python graphrag/experiments/agentic/holdout_expected.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EXP_DIR.parents[1] / "schema"))

from common import WORK_DIR, load_chunks, load_json_confirmed, make_llm, precheck, setup_console, write_json  # noqa: E402
from step6_expected import draft_one  # noqa: E402

SCHEMA_FILE = WORK_DIR / "schema-draft-r2.confirmed.json"   # 최종 스키마와 같은 초안
HOLDOUT_FILE = EXP_DIR / "holdout-expected.json"
REVIEW_FILE = EXP_DIR / "holdout-expected.md"

# 스키마 설계 표본·채우기 시험에 쓰지 않은 청크만 고름
HOLDOUT = [
    {"chunk_index": 103, "kind": "역량질문 관련", "reason": "제128조 손해배상청구권(CQ09와 같은 구제 패턴)"},
    {"chunk_index": 109, "kind": "역량질문 관련", "reason": "제132조의2 특허취소신청(청구 주체·기한·처리 기관 패턴)"},
    {"chunk_index": 117, "kind": "역량질문 관련", "reason": "제133조 특허무효심판(청구 주체·기한 패턴)"},
    {"chunk_index": 188, "kind": "역량질문 관련", "reason": "제227 ~ 229조의3 위증죄 등(CQ07과 같은 형벌 패턴)"},
    {"chunk_index": 5, "kind": "역량질문 무관", "reason": "제13 ~ 14조 재판관할·기간 계산(행정 조문)"},
    {"chunk_index": 71, "kind": "역량질문 무관", "reason": "제85 ~ 86조 특허원부·특허증 발급(행정 조문)"},
]


def review_md(data: dict) -> str:
    lines = ["# 검증용(holdout) 기대 결과", "", f"- 상태: {data['status']}",
             "- 확정 방법: holdout-expected.json을 검토해 고친 뒤 `\"status\": \"확정\"`으로 바꿈", ""]
    for c in data["chunks"]:
        lines += [f"## chunk {c['chunk_index']} ({c['kind']}) — {c['reason']}", ""]
        if not c["nodes"]:
            lines += ["- 기대 결과 없음", ""]
            continue
        lines += [f"- 노드: " + "; ".join(f"{n['type']}:{n['name']}" for n in c["nodes"])]
        lines += [f"- 관계: {r['start']['name']} -{r['type']}-> {r['end']['name']}" for r in c["relationships"]]
        lines.append("")
    return "\n".join(lines) + "\n"


async def main() -> None:
    setup_console()
    precheck()
    if HOLDOUT_FILE.exists() and json.loads(HOLDOUT_FILE.read_text(encoding="utf-8")).get("status") == "확정":
        sys.exit(f"[건너뜀] {HOLDOUT_FILE.name}이(가) 이미 확정됨")
    draft = load_json_confirmed(SCHEMA_FILE, "스키마 초안")
    schema = draft["schema"]
    schema_text = json.dumps({k: schema[k] for k in ("node_types", "relationship_types", "patterns")},
                             ensure_ascii=False, indent=1)
    by_idx = {c["chunk_index"]: c for c in load_chunks()}
    llm = make_llm(reasoning_effort="medium", max_tokens=16000, json_mode=False)
    related = [h for h in HOLDOUT if h["kind"] == "역량질문 관련"]
    drafts = await asyncio.gather(*(draft_one(llm, by_idx[h["chunk_index"]], schema, schema_text) for h in related))
    by_rel = {h["chunk_index"]: d for h, d in zip(related, drafts)}
    chunks = []
    for h in HOLDOUT:
        d = by_rel.get(h["chunk_index"], {"nodes": [], "relationships": []})
        chunks.append({**h, "expected": "있음" if h["chunk_index"] in by_rel else "없음",
                       "nodes": d["nodes"], "relationships": d["relationships"]})
    data = {"status": "초안", "schema_round": draft["round"],
            "match_rule": "fill-test-expected.json과 같음(이름 정규화 후 비교, aliases 인정)", "chunks": chunks}
    write_json(HOLDOUT_FILE, data)
    REVIEW_FILE.write_text(review_md(data), encoding="utf-8")
    print(REVIEW_FILE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
