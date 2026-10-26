"""8단계: 스키마 설계 확정.

사람이 최종 스키마로 정한 초안(schema-draft.json)과 채우기 시험 회차 결과를 읽어
- schema.json: 인덱싱 단계에서 `GraphSchema.from_file()`로 그대로 불러올 JSON
- schema-design.md: 노드·관계 타입 설명, 역량질문별 패턴 경로, 시험 결과, 한계를 담은 설계서
- schema-design.mmd: 스키마 그림(mermaid). 영문 이름 뒤 괄호에 한글 병기
를 만들고, schema.json을 GraphSchema로 다시 불러와 검증함(로그: work/schema-load.log).

실행:
    python step8_finalize.py --round 5   # 최종 판정에 쓴 채우기 시험 회차
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime

from neo4j_graphrag.components.schema import GraphSchema

from common import (
    DESIGN_FILE,
    DRAFT_FILE,
    MODEL_NAME,
    SCHEMA_DIR,
    SCHEMA_FILE,
    WORK_DIR,
    load_competency_questions,
    load_json_confirmed,
    setup_console,
)

MERMAID_FILE = SCHEMA_DIR / "schema-design.mmd"
LOAD_LOG = WORK_DIR / "schema-load.log"

NODE_KO = {
    "Actor": "주체", "Right": "권리", "Invention": "발명 유형", "Procedure": "절차", "Decision": "결정",
    "Period": "기간", "Penalty": "형벌", "Exclusion": "효력 제외 사유", "Remedy": "구제 수단",
}
REL_KO = {
    "ELIGIBLE_FOR": "특허를 받을 수 있음", "NOT_ELIGIBLE_FOR": "특허를 받을 수 없음", "CAN_FILE": "청구할 수 있음",
    "HANDLED_BY": "처리 기관", "TARGETS": "불복 대상", "HAS_DEADLINE": "기한", "HAS_DURATION": "존속기간",
    "NOT_EFFECTIVE_FOR": "효력이 미치지 않음", "HOLDS": "권리를 가짐", "CAN_CLAIM": "청구할 수 있는 구제",
    "CLAIMED_AGAINST": "청구 상대방", "PUNISHED_BY": "형벌을 받음",
}

# 최종 판정 이후 사람이 정한 한계와 권고(채우기 시험 보고서·회차 원인 추정에서 옮김)
LIMITATIONS = [
    "지어냄 비율 기준(0.1 이하) 미달: 최종 스키마의 3회 평균 지어냄 비율 0.294. 프롬프트의 완료조건 중 '기준을 넘은 회차가 있음'은 충족하지 못했고, 사용자 결정으로 가장 나은 스키마를 최종안으로 확정함",
    "남은 지어냄의 출처: 역량질문과 무관한 행정 조문(전자문서 이용신고 등)에서 뽑힌 절차·기관, 관계 없이 혼자 남은 주체(고립 노드), 존속기간 연장 같은 범위 밖 기간, '~한 경우' 조건문 형태의 이름 변형. 스키마 설명의 [뽑지 않음] 규칙을 추출 LLM이 잘 따르지 않음",
    "권고(인덱싱 단계): 추출 뒤 엔터티 관계가 하나도 없는 고립 노드를 지우는 후처리를 둘 것. 저장된 추출 결과로 계산하면 지어냄 비율이 0.274에서 0.206으로 내려가고 재현율은 그대로임(현재 기대 결과 기준)",
    "권고(인덱싱 단계): 0.1 이하가 필요하면 추출 전에 청크가 역량질문 범위인지 판별하는 단계를 검토할 것",
    "실행 변동: 추출 LLM은 temperature 0에서도 결과가 실행마다 달라, 같은 설정에서 청크 하나가 11건 모두 맞음과 0건 사이를 오감. 채우기 시험은 3회 반복 평균으로 판정함",
    "CQ04(거절결정 불복 → 심판 → 특허심판원)는 3회 중 1회만 이어짐. 불복 주체 이름이 줄거나 특허심판원으로 가는 HANDLED_BY가 빠지는 경우가 있음",
    "이름 규칙 불일치: 최종 스키마의 이름 규칙은 '또는'으로 묶인 표현을 나누지 않지만, 확정 기대 결과는 '발명을 한 사람'과 '그 승계인'을 나눔(4·7회차에서 고친 규칙은 최종안에 넣지 않음)",
    "기대 결과 범위 불일치: 기대 결과 chunk 73에는 제88조② 존속기간 기산점이 있지만(7회차 때 추가), 최종 스키마 설명은 존속기간의 예외적 기산점을 뽑지 않도록 되어 있음",
    "범위: 시험용 청크 10건(관련 8, 무관 2)으로만 검증함. 역량질문 9개 범위 밖의 조문(국제출원, 재심 절차 세부 등)은 담지 않음",
]


def to_mermaid(schema: GraphSchema) -> str:
    lines = ["flowchart LR"]
    for n in schema.node_types:
        props = "<br/>".join(f"{p.name}: {p.type}" for p in n.properties)
        lines.append(f'    {n.label}["{n.label} ({NODE_KO.get(n.label, "")})<br/>{props}"]')
    for p in schema.patterns:
        lines.append(f'    {p.source} -- "{p.relationship} ({REL_KO.get(p.relationship, "")})" --> {p.target}')
    return "\n".join(lines) + "\n"


def render_design(draft: dict, schema: GraphSchema, cqs: list[dict], rnd: dict, mermaid: str) -> str:
    m = rnd["metrics"]
    rr = f" (실행별 {m['recall_range'][0]:.3f} ~ {m['recall_range'][1]:.3f})" if rnd.get("repeat", 1) > 1 else ""
    hr = f" (실행별 {m['hallucination_range'][0]:.3f} ~ {m['hallucination_range'][1]:.3f})" if rnd.get("repeat", 1) > 1 else ""
    out = [
        "# 특허법 GraphRAG 스키마 설계서",
        "",
        f"- 작성일: {datetime.now():%Y-%m-%d}",
        "- 스키마 파일: `schema.json` (neo4j-graphrag `GraphSchema.from_file()`로 그대로 불러옴. "
        "라이브러리가 OS 기본 인코딩으로 읽으므로 Windows(cp949)에서도 읽히도록 한글을 유니코드 이스케이프로 저장함)",
        f"- 근거: 확정 역량질문 {len(cqs)}개, 스키마 초안 {draft['round']}회차({draft.get('source', 'LLM')}), 채우기 시험 {rnd['round']}회차",
        "",
        "## 1. 용어",
        "",
        "- 그래프 스키마(graph schema): 지식 그래프에 담을 노드 타입·관계 타입·패턴의 약속. LLM이 문서에서 개체를 뽑을 때 기준이 됨",
        "- 노드 타입(node type): 그래프의 점(개체) 종류. 예: Actor(주체)",
        "- 관계 타입(relationship type): 점을 잇는 선의 종류. 예: CAN_FILE(청구할 수 있음)",
        "- 패턴(pattern): `시작 노드 타입 -관계 타입-> 끝 노드 타입`. 어떤 관계가 어떤 노드 사이에만 올 수 있는지 정함",
        "- 역량질문(competency question): 그래프가 답할 수 있어야 하는 질문 목록. 스키마 범위와 검증 기준이 됨",
        "- 채우기 시험(fill test): 스키마대로 실제 청크에서 추출해 사람이 확정한 기대 결과와 비교하는 시험",
        "",
        "## 2. 스키마 그림",
        "",
        "```mermaid",
        mermaid.rstrip(),
        "```",
        "",
        "- 같은 내용의 mermaid 원본: `schema-design.mmd`",
        "- 스키마 밖 타입·관계·패턴은 모두 허용하지 않음(`additional_node_types`·`additional_relationship_types`·`additional_patterns` = False)",
        "",
        f"## 3. 노드 타입 ({len(schema.node_types)}개)",
        "",
        "| 노드 타입 | 속성 | 추출 기준 설명 |",
        "|---|---|---|",
    ]
    # 필수 속성은 EXISTENCE 제약으로 표현됨(PropertyType.required는 deprecated)
    required = {(c.node_type, name) for c in schema.constraints
                if str(getattr(c.type, "value", c.type)) == "EXISTENCE" for name in c.property_names}
    for n in schema.node_types:
        props = "<br>".join(f"`{p.name}`({p.type}{', 필수' if (n.label, p.name) in required else ''}): {p.description}"
                            for p in n.properties)
        out.append(f"| {n.label} ({NODE_KO.get(n.label, '')}) | {props} | {n.description} |")
    out += ["", f"## 4. 관계 타입 ({len(schema.relationship_types)}개)", "", "| 관계 타입 | 패턴 | 추출 기준 설명 |", "|---|---|---|"]
    for r in schema.relationship_types:
        pats = "<br>".join(f"{p.source} → {p.target}" for p in schema.patterns if p.relationship == r.label)
        out.append(f"| {r.label} ({REL_KO.get(r.label, '')}) | {pats} | {r.description} |")
    cq_ans = {q["cq_id"]: q for q in rnd["cq"]}
    out += ["", "## 5. 역량질문별 패턴 경로", "", "| ID | 역량질문 | 패턴 경로 | 시험에서 이어짐 | 설명 |", "|---|---|---|---|---|"]
    for c in draft["cq_paths"]:
        q = cq_ans.get(c["cq_id"], {})
        ok = ("O" if q.get("answerable") else "X") + (f" ({q['runs_ok']})" if q.get("runs_ok") else "")
        out.append(f"| {c['cq_id']} | {c['question']} | `{c['path']}` | {ok} | {c['explanation']} |")
    out += [
        "",
        "## 6. 채우기 시험 결과(최종 판정)",
        "",
        f"- 회차: {rnd['round']}회차, {rnd.get('label', '')}",
        f"- 추출: `LLMEntityRelationExtractor`({MODEL_NAME}, 추출 프롬프트: {rnd.get('prompt_template')}) → `GraphPruning`",
        f"- 재현율(기대 항목 중 뽑힌 비율): {m['recall']:.3f}{rr} — 기준 0.8 이상 **충족**",
        f"- 지어냄 비율(뽑힌 항목 중 기대에 없는 비율): {m['hallucination_rate']:.3f}{hr} — 기준 0.1 이하 **미달**",
        f"- 역량질문 답 가능: {m['cq_answerable']}/{m['cq_total']}(3회 중 2회 이상 이어짐) — 기준 모두 답 가능 **미달**(CQ04)",
        "- 채점 기준: 이 회차는 7회차 이전의 기대 결과(work/fill-test-expected.v1.json)로 채점함. "
        "현재 기대 결과(chunk 73에 제88조② 기산점 추가)로 저장 결과를 다시 계산하면 재현율 0.873, 지어냄 비율 0.274",
        "- 회차별 전체 결과와 원인 추정: `fill-test-report.md`",
        "",
        "## 7. 한계와 권고",
        "",
    ]
    out += [f"- {x}" for x in LIMITATIONS]
    out += [
        "",
        "## 8. 인덱싱 단계에서 쓰는 법",
        "",
        "```python",
        "from neo4j_graphrag.components.schema import GraphSchema",
        "from neo4j_graphrag.experimental.pipeline.kg_builder import SimpleKGPipeline",
        "",
        'schema = GraphSchema.from_file("graphrag/schema/schema.json")',
        "kg_builder = SimpleKGPipeline(llm=llm, driver=driver, embedder=embedder, schema=schema)",
        "# 추출 프롬프트는 라이브러리 기본값(채우기 시험과 같은 조건)",
        "```",
    ]
    return "\n".join(out) + "\n"


def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, required=True, help="최종 판정에 쓴 채우기 시험 회차")
    args = parser.parse_args()

    draft = load_json_confirmed(DRAFT_FILE, "스키마 초안")
    cqs = load_competency_questions()
    rnd = json.loads((WORK_DIR / f"round-{args.round}.json").read_text(encoding="utf-8"))
    if rnd.get("schema_round") != draft["round"]:
        sys.exit(f"[중단] {args.round}회차 시험의 스키마({rnd.get('schema_round')}회차)와 최종 초안({draft['round']}회차)이 다름")

    schema = GraphSchema.model_validate(draft["schema"])
    # GraphSchema.from_file()은 OS 기본 인코딩으로 읽음(Windows는 cp949) → 한글을 \uXXXX로 이스케이프해 어느 환경에서도 읽히게 함
    SCHEMA_FILE.write_text(json.dumps(schema.model_dump(mode="json"), ensure_ascii=True, indent=2), encoding="ascii")

    # schema.json을 인덱싱 단계와 같은 방식으로 다시 불러와 검증
    WORK_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOAD_LOG, mode="w", encoding="utf-8"), logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("schema-load")
    loaded = GraphSchema.from_file(SCHEMA_FILE)
    assert loaded == schema, "다시 불러온 스키마가 원본과 다름"
    log.info("GraphSchema.from_file(%s) 성공: 노드 타입 %d, 관계 타입 %d, 패턴 %d", SCHEMA_FILE.name,
             len(loaded.node_types), len(loaded.relationship_types), len(loaded.patterns))
    log.info("additional_node_types=%s, additional_relationship_types=%s, additional_patterns=%s",
             loaded.additional_node_types, loaded.additional_relationship_types, loaded.additional_patterns)
    log.info("노드 타입: %s", ", ".join(n.label for n in loaded.node_types))
    log.info("관계 타입: %s", ", ".join(r.label for r in loaded.relationship_types))

    mermaid = to_mermaid(loaded)
    MERMAID_FILE.write_text(mermaid, encoding="utf-8")
    DESIGN_FILE.write_text(render_design(draft, loaded, cqs, rnd, mermaid), encoding="utf-8")
    print(f"[완료] {SCHEMA_FILE.name}, {DESIGN_FILE.name}, {MERMAID_FILE.name}, 불러오기 로그 → {LOAD_LOG}")


if __name__ == "__main__":
    main()
