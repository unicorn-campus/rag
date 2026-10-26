"""에이전틱 추출 실험 공통 틀.

- 청크 본문: 벡터DB를 한 번만 읽어 results/chunks.json에 캐시(chromadb가 읽기만 해도 vectordb/를 갱신하므로)
- 채점: 원래 채우기 시험(step7_fill_test.py)의 채점 함수를 그대로 씀(읽기만 함)
- 실행: 방안(pipeline) 함수 하나를 개발용 10건 또는 검증용 6건에 N회 돌려 평균·범위·LLM 호출 수를 기록
원래 프로그램(graphrag/schema/, graphrag/index/)은 고치지 않음. 출력은 이 폴더에만 씀.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Awaitable, Callable

EXP_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = EXP_DIR.parents[1] / "schema"
sys.path.insert(0, str(SCHEMA_DIR))

from common import (  # noqa: E402
    EXPECTED_FILE,
    WORK_DIR,
    load_json_confirmed,
    make_llm,
    write_json,
)
from neo4j_graphrag.components.schema import GraphSchema  # noqa: E402
from neo4j_graphrag.components.types import Neo4jGraph  # noqa: E402
from step7_fill_test import aggregate_runs, score_run  # noqa: E402

RESULTS_DIR = EXP_DIR / "results"
CHUNKS_CACHE = RESULTS_DIR / "chunks.json"
HOLDOUT_FILE = EXP_DIR / "holdout-expected.json"
SCHEMA_DRAFT = WORK_DIR / "schema-draft-r2.confirmed.json"   # 최종 스키마와 같은 초안(역량질문 패턴 경로 포함)
CONCURRENCY = 4

Pipeline = Callable[["Context", dict], Awaitable[Neo4jGraph]]


# ---------------------------------------------------------------------------
# 입력
# ---------------------------------------------------------------------------
def load_chunks_cached() -> dict[int, str]:
    if not CHUNKS_CACHE.exists():
        from common import load_chunks

        write_json(CHUNKS_CACHE, {str(c["chunk_index"]): c["text"] for c in load_chunks()})
    return {int(k): v for k, v in json.loads(CHUNKS_CACHE.read_text(encoding="utf-8")).items()}


def load_sets() -> dict[str, dict]:
    return {"dev": load_json_confirmed(EXPECTED_FILE, "기대 추출 결과"),
            "holdout": load_json_confirmed(HOLDOUT_FILE, "검증용 기대 결과")}


# ---------------------------------------------------------------------------
# LLM 호출 수 세기
# ---------------------------------------------------------------------------
def counting(llm):
    """neo4j-graphrag LLM 인스턴스의 ainvoke를 감싸 호출 수를 셈(llm.calls). 타입 검사를 통과하도록 인스턴스를 그대로 씀."""
    original = llm.ainvoke
    llm.calls = 0

    async def ainvoke(*args, **kwargs):
        llm.calls += 1
        return await original(*args, **kwargs)

    llm.ainvoke = ainvoke
    return llm


class Context:
    """한 번의 실행(run)에서 방안이 쓰는 자원."""

    def __init__(self, schema: GraphSchema, draft: dict, chunks: dict[int, str]) -> None:
        self.schema = schema
        self.draft = draft
        self.chunks = chunks
        self.json_llm = counting(make_llm(reasoning_effort="medium"))
        self.text_llm = counting(make_llm(reasoning_effort="medium", max_tokens=16000, json_mode=False))
        self.failures: list[str] = []   # 다시 시도해도 실패한 단계(청크 번호와 단계 이름)
        self.sem = asyncio.Semaphore(CONCURRENCY)

    @property
    def calls(self) -> int:
        return self.json_llm.calls + self.text_llm.calls


# ---------------------------------------------------------------------------
# 실행과 채점
# ---------------------------------------------------------------------------
def score_graphs(draft: dict, expected: dict, graphs: list[Neo4jGraph]) -> dict:
    return score_run(draft, expected, graphs, [""] * len(graphs), Counter(), Counter())


async def run_pipeline(name: str, pipeline: Pipeline, split: str, repeat: int = 3) -> dict:
    """방안을 split(dev/holdout)에 repeat회 돌려 채점하고 results/{name}-{split}.json에 저장함."""
    draft = load_json_confirmed(SCHEMA_DRAFT, "스키마 초안")
    schema = GraphSchema.model_validate(draft["schema"])
    expected = load_sets()[split]
    chunks = load_chunks_cached()
    runs, raw, calls, failures = [], [], [], []
    started = time.time()
    for i in range(repeat):
        ctx = Context(schema, draft, chunks)
        units = [{"chunk_index": c["chunk_index"], "text": chunks[c["chunk_index"]]} for c in expected["chunks"]]
        graphs = await asyncio.gather(*(pipeline(ctx, u) for u in units))
        runs.append(score_graphs(draft, expected, list(graphs)))
        raw.append([g.model_dump() for g in graphs])
        calls.append(ctx.calls)
        failures.append(ctx.failures)
        m = runs[-1]["metrics"]
        print(f"  [{name}/{split}] 실행 {i + 1}/{repeat}: 재현율 {m['recall']:.3f}, 지어냄 {m['hallucination_rate']:.3f}, "
              f"역량질문 {m['cq_answerable']}/{m['cq_total']}, LLM {ctx.calls}회", flush=True)
    agg = aggregate_runs(runs)
    result = {"name": name, "split": split, "repeat": repeat, "elapsed_sec": round(time.time() - started),
              "llm_calls_per_run": calls, "llm_calls_per_chunk": sum(calls) / repeat / len(expected["chunks"]),
              "failures": failures,
              "metrics": agg["metrics"], "cq": agg["cq"], "chunks": agg["chunks"], "raw_graphs": raw}
    write_json(RESULTS_DIR / f"{name}-{split}.json", result)
    m = agg["metrics"]
    print(f"[{name}/{split}] 평균 재현율 {m['recall']:.3f}, 지어냄 {m['hallucination_rate']:.3f}, "
          f"역량질문 {m['cq_answerable']}/{m['cq_total']}, 청크당 LLM {result['llm_calls_per_chunk']:.1f}회", flush=True)
    return result


async def run_interleaved(pipes: dict[str, Pipeline], split: str, repeat: int) -> dict[str, dict]:
    """여러 방안을 실행 회차마다 번갈아 돌림(같은 시간대 짝지은 비교, Groq 상태 변화의 영향을 고르게 받게 함)."""
    draft = load_json_confirmed(SCHEMA_DRAFT, "스키마 초안")
    schema = GraphSchema.model_validate(draft["schema"])
    expected = load_sets()[split]
    chunks = load_chunks_cached()
    units = [{"chunk_index": c["chunk_index"], "text": chunks[c["chunk_index"]]} for c in expected["chunks"]]
    acc = {name: {"runs": [], "raw": [], "calls": [], "failures": [], "sec": 0.0} for name in pipes}
    for i in range(repeat):
        for name, pipeline in pipes.items():
            started = time.time()
            ctx = Context(schema, draft, chunks)
            graphs = list(await asyncio.gather(*(pipeline(ctx, u) for u in units)))
            a = acc[name]
            a["runs"].append(score_graphs(draft, expected, graphs))
            a["raw"].append([g.model_dump() for g in graphs])
            a["calls"].append(ctx.calls)
            a["failures"].append(ctx.failures)
            a["sec"] += time.time() - started
            m = a["runs"][-1]["metrics"]
            print(f"  [{name}/{split}] 실행 {i + 1}/{repeat}: 재현율 {m['recall']:.3f}, 지어냄 {m['hallucination_rate']:.3f}, "
                  f"역량질문 {m['cq_answerable']}/{m['cq_total']}, LLM {ctx.calls}회, 실패 {len(ctx.failures)}", flush=True)
    out = {}
    for name, a in acc.items():
        agg = aggregate_runs(a["runs"])
        out[name] = {"name": name, "split": split, "repeat": repeat, "elapsed_sec": round(a["sec"]),
                     "llm_calls_per_run": a["calls"], "llm_calls_per_chunk": sum(a["calls"]) / repeat / len(units),
                     "failures": a["failures"], "metrics": agg["metrics"], "cq": agg["cq"], "chunks": agg["chunks"],
                     "per_run": [r["metrics"] for r in a["runs"]], "raw_graphs": a["raw"]}
        write_json(RESULTS_DIR / f"{name}-{split}.json", out[name])
        m = agg["metrics"]
        print(f"[{name}/{split}] 평균 재현율 {m['recall']:.3f}({m['recall_range'][0]:.3f}~{m['recall_range'][1]:.3f}), "
              f"지어냄 {m['hallucination_rate']:.3f}({m['hallucination_range'][0]:.3f}~{m['hallucination_range'][1]:.3f}), "
              f"역량질문 {m['cq_answerable']}/{m['cq_total']}, 청크당 LLM {out[name]['llm_calls_per_chunk']:.1f}회", flush=True)
    return out


def rescore(name: str, split: str, transform: Callable[[Neo4jGraph, int], Neo4jGraph] | None = None,
            new_name: str | None = None) -> dict:
    """저장된 원자료(raw_graphs)를 LLM 호출 없이 다시 채점함. transform으로 규칙 후처리를 얹을 수 있음."""
    src = json.loads((RESULTS_DIR / f"{name}-{split}.json").read_text(encoding="utf-8"))
    draft = load_json_confirmed(SCHEMA_DRAFT, "스키마 초안")
    expected = load_sets()[split]
    runs, raw = [], []
    for run in src["raw_graphs"]:
        graphs = [Neo4jGraph.model_validate(g) for g in run]
        if transform:
            graphs = [transform(g, c["chunk_index"]) for g, c in zip(graphs, expected["chunks"])]
        runs.append(score_graphs(draft, expected, graphs))
        raw.append([g.model_dump() for g in graphs])
    agg = aggregate_runs(runs)
    result = {**{k: src[k] for k in ("repeat", "llm_calls_per_run", "llm_calls_per_chunk")},
              "name": new_name or name, "split": split, "derived_from": name,
              "metrics": agg["metrics"], "cq": agg["cq"], "chunks": agg["chunks"], "raw_graphs": raw}
    if new_name:
        write_json(RESULTS_DIR / f"{new_name}-{split}.json", result)
    return result
