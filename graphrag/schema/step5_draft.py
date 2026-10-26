"""5단계: 스키마 초안 확정(LLM 초안 → 사람 확정).

합친 후보 목록(candidates/merged.json)과 확정 역량질문을 LLM에 주고 스키마 초안을 받음.
LLM이 돌려주는 것
- 추가·제거·병합할 후보와 각각의 근거
- 노드 타입과 관계 타입마다 추출 기준이 되는 구체적 설명(한국어)
- 역량질문마다 답에 쓰이는 패턴 경로
2회차부터는 직전 채우기 시험 결과(work/round-{N-1}.json)를 함께 주어 설명·속성·패턴을 고치게 함.

출력: schema-draft.json(status: 초안), schema-draft.md(사람 검토용)
      사람이 검토 후 schema-draft.json의 "status"를 "확정"으로 바꾸면 다음 단계에서 읽음

실행:
    python step5_draft.py --round 1
    python step5_draft.py --round 2   # 직전 회차 시험 결과를 반영해 고침
    python step5_draft.py --round 1 --from work/schema-draft-r1.reviewed.json
                                      # 사람이 검토·수정한 초안(LLM 출력과 같은 형식)을 같은 검사·변환에 통과시킴
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys

from neo4j_graphrag.components.schema import GraphSchema

from common import (
    DRAFT_FILE,
    MERGED_JSON,
    SCHEMA_DIR,
    WORK_DIR,
    ask_json,
    load_competency_questions,
    make_llm,
    precheck,
    setup_console,
    write_json,
)

DRAFT_MD = SCHEMA_DIR / "schema-draft.md"
PROPERTY_TYPES = "BOOLEAN, DATE, DURATION, FLOAT, INTEGER, LIST, STRING"

PROMPT = """[목표]
특허법 문서로 GraphRAG 인덱싱을 하기 위하여, 아래 역량질문에 답할 수 있는 그래프 스키마 초안을 작성함

[역할]
지식그래프 스키마 설계 경험이 있는 데이터 엔지니어

[맥락]
- 후보 목록은 특허법 표본 청크 {n_samples}건에서 LLM이 따로 뽑은 스키마 후보를 합친 것. 등장 수는 후보가 나온 표본 수
- "합칠 후보"는 이름이 다르지만 뜻이 같아 보이는 묶음(아직 합치지 않음)
- 이 스키마는 LLMEntityRelationExtractor 프롬프트에 그대로 들어가 청크마다 노드·관계를 뽑는 기준이 됨
- 사용자는 법률 비전문가 교육생. 역량질문에 답하는 데 쓰이지 않는 개념은 범위 밖임
- (중요) 인덱싱할 문서는 특허법 조문 자체임. 특정 출원·특허·사람·회사 같은 개별 사례(출원번호, 등록번호, 실명, 실제 날짜)는 나오지 않음.
  노드는 조문이 규정하는 일반 개념(주체 유형, 청구·신청·심판 같은 절차, 결정·처분, 권리, 요건, 기간, 행위, 형벌, 구제 수단 등)이고,
  `name`은 조문 원문 표현을 씀(예: "특허권자", "출원심사의 청구", "특허거절결정", "특허심판원", "특허출원일부터 3년 이내").
  기간·기한·금액도 실제 날짜가 아니라 조문 표현 문자열로 담음
- 후보 목록에는 개별 사례를 가정한 후보(출원번호, 등록일 등)가 섞여 있음. 조문 일반 개념에 맞게 고쳐 씀
- 질문이 "누가/누구에게/무엇을/언제까지"를 함께 묻는 경우 각 답이 별도 노드로 이어지는 다중 단계 경로가 되게 함
- 채우기 시험: 시험용 청크에서 뽑힌 노드(타입, name)와 관계(시작, 타입, 끝)를 사람이 확정한 기대 결과와 비교함.
  역량질문과 무관한 청크(예: 전자문서 제출, 서류 송달, 부칙)에서는 아무것도 뽑히지 않아야 함
{feedback}
[처리]
1. 역량질문마다 답에 필요한 정보 묶음을 정하고, 후보 중 쓸 것·뺄 것·합칠 것을 정함. 후보에 없는 타입도 필요하면 추가함
2. 노드 타입: 영문 단수 PascalCase. 관계 타입: 영문 UPPER_SNAKE_CASE. 설명은 한국어
3. 노드 타입마다 속성을 1개 이상 둠. 모든 노드 타입은 식별용 `name`(STRING, required)을 가짐. 그 밖의 속성은 역량질문 답에 필요할 때만 둠
   (속성 타입: {ptypes})
4. 노드 타입·관계 타입 설명은 추출 기준이 되게 구체적으로 씀
   - 무엇을 넣고 무엇을 빼는지(경계), 법조문 속 실제 예 2개 이상
   - `name` 값을 쓰는 규칙(예: 조문 원문 명사구를 그대로, 조사·괄호 속 한자 제외, 조문 번호는 넣지 않음)
   - 관계 타입은 방향(시작 → 끝)과 어떤 문장 표현일 때 쓰는지
5. 패턴은 실제로 쓰는 (시작 노드 타입, 관계 타입, 끝 노드 타입)만 둠. 모든 노드 타입은 패턴에 1번 이상 나옴
6. 역량질문마다 답에 쓰이는 패턴 경로를 `노드 타입 -관계 타입-> 노드 타입 ...` 형식으로 씀
7. 역량질문에 답하는 데 쓰이지 않는 행정 절차(서류 제출 방식, 송달, 수수료 납부 방식 등)는 노드 타입 설명에서 명시적으로 제외함
8. 노드 타입은 10개 이하, 관계 타입은 12개 이하로 최소화함
9. 출력 전 자기 점검
   - 경로가 질문의 모든 의문사(누가, 누구에게, 어떤, 무엇, 언제)에 답하는가. 빠진 의문사가 있으면 노드·관계를 더 이음
   - 질문의 주어(특허권, 발명, 특허권자, 심사청구 등)와 경로 시작 노드 타입의 뜻이 맞는가
   - 뜻이 겹치는 노드 타입이 없는가(예: 주체와 기관을 나눈다면 경계를 설명에 씀)
   - 설명의 예가 특허법 조문에 실제로 나오는 표현인가(개별 사례 이름·날짜 금지)

[출력]
JSON 객체 하나만 출력:
{{
  "decisions": [{{"action": "추가|제거|병합|유지", "candidates": ["후보 이름"], "result": "스키마에서의 이름(제거면 빈 문자열)", "reason": "근거"}}],
  "node_types": [{{"label": "PascalCase", "description": "추출 기준 설명", "properties": [{{"name": "name", "type": "STRING", "description": "값 규칙", "required": true}}]}}],
  "relationship_types": [{{"label": "UPPER_SNAKE_CASE", "description": "추출 기준 설명"}}],
  "patterns": [{{"source": "노드 타입", "relationship": "관계 타입", "target": "노드 타입"}}],
  "cq_paths": [{{"cq_id": "CQ01", "question": "질문", "path": "A -REL-> B", "explanation": "이 경로로 답이 나오는 이유"}}],
  "changes": "직전 회차 대비 고친 내용 요약(1회차는 '최초 초안')"
}}

<역량질문>
{cqs}
</역량질문>

<노드타입후보 형식="이름 | 등장 수 | 속성">
{nodes}
</노드타입후보>

<관계타입후보 형식="이름 | 등장 수">
{rels}
</관계타입후보>

<패턴후보 형식="시작 -관계-> 끝 | 등장 수">
{patterns}
</패턴후보>

<합칠후보>
{merge}
</합칠후보>
{current}"""

FEEDBACK = """- 이번은 {round}회차 초안임. 직전 회차 스키마로 채우기 시험을 했고 기준(재현율 0.8 이상, 지어냄 비율 0.1 이하, 역량질문 모두 답 가능)을 넘지 못함
- 노드 타입·관계 타입·패턴의 이름은 되도록 유지하고(기대 결과와 비교하므로), 설명·속성·패턴을 고쳐 아래 문제를 줄임
<직전시험결과>
{result}
</직전시험결과>
"""


def validate_names(draft: dict) -> list[str]:
    """이름 규칙·속성·패턴 일관성 검사. 문제 목록을 돌려줌."""
    errs = []
    labels = {n["label"] for n in draft["node_types"]}
    rels = {r["label"] for r in draft["relationship_types"]}
    for n in draft["node_types"]:
        if not re.fullmatch(r"[A-Z][a-zA-Z0-9]*", n["label"]):
            errs.append(f"노드 타입 이름 규칙 위반: {n['label']}")
        if not n.get("properties"):
            errs.append(f"속성 없음: {n['label']}")
    for r in rels:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", r):
            errs.append(f"관계 타입 이름 규칙 위반: {r}")
    used = set()
    for p in draft["patterns"]:
        if p["source"] not in labels or p["target"] not in labels or p["relationship"] not in rels:
            errs.append(f"정의되지 않은 타입을 쓰는 패턴: {p}")
        used |= {p["source"], p["target"]}
    errs += [f"패턴에 안 나오는 노드 타입: {n}" for n in labels - used]
    return errs


def to_graph_schema(draft: dict) -> GraphSchema:
    """LLM 초안을 neo4j-graphrag GraphSchema로 변환(스키마 밖 타입·관계·패턴은 모두 불허)."""
    return GraphSchema.model_validate({
        "node_types": [
            {
                "label": n["label"],
                "description": n["description"],
                "properties": [
                    {"name": p["name"], "type": p.get("type", "STRING"), "description": p.get("description", ""),
                     "required": bool(p.get("required", False))}
                    for p in n["properties"]
                ],
                "additional_properties": False,
            }
            for n in draft["node_types"]
        ],
        "relationship_types": [{"label": r["label"], "description": r["description"]} for r in draft["relationship_types"]],
        "patterns": [{"source": p["source"], "relationship": p["relationship"], "target": p["target"]} for p in draft["patterns"]],
        "additional_node_types": False,
        "additional_relationship_types": False,
        "additional_patterns": False,
    })


def render_md(d: dict) -> str:
    s = d["schema"]
    out = [f"# 스키마 초안 ({d['round']}회차)", "", f"- 상태: {d['status']} (확정하면 schema-draft.json의 status를 \"확정\"으로 바꿈)",
           f"- 출처: {d.get('source', 'LLM')}", f"- 고친 내용: {d.get('changes', '최초 초안')}", "",
           "## 1. 후보 처리(추가·제거·병합)", "", "| 처리 | 후보 | 스키마 이름 | 근거 |", "|---|---|---|---|"]
    out += [f"| {x['action']} | {', '.join(x['candidates'])} | {x['result']} | {x['reason']} |" for x in d["decisions"]]
    out += ["", "## 2. 노드 타입", "", "| 노드 타입 | 설명(추출 기준) | 속성 |", "|---|---|---|"]
    for n in s["node_types"]:
        props = "<br>".join(f"`{p['name']}`({p['type']}): {p['description']}" for p in n["properties"])
        out.append(f"| {n['label']} | {n['description']} | {props} |")
    out += ["", "## 3. 관계 타입", "", "| 관계 타입 | 설명(추출 기준) |", "|---|---|"]
    out += [f"| {r['label']} | {r['description']} |" for r in s["relationship_types"]]
    out += ["", "## 4. 패턴", ""]
    out += [f"- `({p['source']})-[:{p['relationship']}]->({p['target']})`" for p in s["patterns"]]
    out += ["", "## 5. 역량질문별 패턴 경로", "", "| ID | 역량질문 | 패턴 경로 | 설명 |", "|---|---|---|---|"]
    out += [f"| {c['cq_id']} | {c['question']} | `{c['path']}` | {c['explanation']} |" for c in d["cq_paths"]]
    return "\n".join(out) + "\n"


async def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--from", dest="from_file", help="검토·수정한 초안 JSON(LLM 호출 없이 변환만 함)")
    args = parser.parse_args()
    precheck(need_llm=not args.from_file)
    if args.from_file:
        draft = json.loads((SCHEMA_DIR / args.from_file).read_text(encoding="utf-8"))
        errs = validate_names(draft)
        if errs:
            sys.exit(f"[중단] 초안 검사 실패: {errs}")
        save(draft, args.round, source=args.from_file)
        return

    cqs = load_competency_questions()
    merged = json.loads(MERGED_JSON.read_text(encoding="utf-8"))
    feedback, current = "", ""
    if args.round > 1:
        prev = WORK_DIR / f"round-{args.round - 1}.json"
        if not prev.exists():
            sys.exit(f"[중단] {prev.name}이(가) 없음. 직전 회차 채우기 시험을 먼저 수행할 것")
        prev_data = json.loads(prev.read_text(encoding="utf-8"))
        feedback = FEEDBACK.format(round=args.round, result=json.dumps(prev_data["feedback"], ensure_ascii=False, indent=1))
        prev_draft = json.loads(DRAFT_FILE.read_text(encoding="utf-8"))
        current = "\n<직전스키마초안>\n" + json.dumps(
            {k: prev_draft[k] for k in ("schema", "cq_paths")}, ensure_ascii=False, indent=1) + "\n</직전스키마초안>\n"

    prompt = PROMPT.format(
        n_samples=merged["n_samples"],
        feedback=feedback,
        ptypes=PROPERTY_TYPES,
        cqs="\n".join(f"{c['id']} ({c['type']}) {c['question']}" for c in cqs),
        nodes="\n".join(f"{n['label']} | {n['count']} | {', '.join(n['properties'])}" for n in merged["node_types"]),
        rels="\n".join(f"{r['label']} | {r['count']}" for r in merged["relationship_types"]),
        patterns="\n".join(f"{p['source']} -{p['relationship']}-> {p['target']} | {p['count']}" for p in merged["patterns"]),
        merge=json.dumps(merged["merge_candidates"], ensure_ascii=False),
        current=current,
    )
    # 출력이 길어 Groq JSON 모드 검증(json_validate_failed)에 걸리기 쉬우므로 JSON 모드를 끄고 직접 파싱함
    llm = make_llm(reasoning_effort="medium", max_tokens=32000, json_mode=False)
    for attempt in range(3):
        draft = await ask_json(llm, prompt)
        errs = validate_names(draft)
        if not errs:
            break
        print(f"  [재시도 {attempt + 1}] 초안 검사 실패: {errs}")
    else:
        sys.exit(f"[중단] 초안 검사 실패: {errs}")

    write_json(WORK_DIR / f"schema-draft-r{args.round}.llm.json", draft)
    save(draft, args.round, source="LLM")


def save(draft: dict, round_no: int, source: str) -> None:
    schema = to_graph_schema(draft)
    data = {
        "status": "초안",
        "round": round_no,
        "source": source,
        "decisions": draft["decisions"],
        "cq_paths": draft["cq_paths"],
        "changes": draft.get("changes", "최초 초안"),
        "schema": schema.model_dump(mode="json"),
    }
    write_json(DRAFT_FILE, data)
    DRAFT_MD.write_text(render_md(data), encoding="utf-8")
    print(f"[완료] {round_no}회차 초안({source}): 노드 타입 {len(schema.node_types)}, 관계 타입 {len(schema.relationship_types)}, "
          f"패턴 {len(schema.patterns)} → {DRAFT_FILE.name}, {DRAFT_MD.name}")


if __name__ == "__main__":
    asyncio.run(main())
