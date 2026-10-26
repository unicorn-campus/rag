"""rerank_rag.py 테스트.

- 단위 테스트: Re-ranking 검색기·비교 모드·체인·인덱스 검증·평가 지표
  (가짜 Re-ranker/LLM/Retriever, API 호출 없음)
- 통합 테스트: 공용 vectordb + bm25 인덱스 검색, Cohere Re-ranking, Groq LLM 답변
  (COHERE_API_KEY, GROQ_API_KEY, vectordb/, bm25/ 필요)

실행: pytest -v
"""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from dotenv import load_dotenv
from langchain_core.documents import BaseDocumentCompressor, Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.retrievers import BaseRetriever

import evaluate_rerank as ev
import rerank_rag as rag


def doc(i: int, text: str | None = None) -> Document:
    return Document(id=f"특허법.pdf::{i:05d}", page_content=text or f"청크 {i}",
                    metadata={"source": "특허법.pdf", "chunk_index": i})


class FakeRetriever(BaseRetriever):
    docs: list[Document]
    queries: list[str] = []

    def _get_relevant_documents(self, query, *, run_manager=None):
        self.queries.append(query)
        return self.docs


class FakeReranker(BaseDocumentCompressor):
    """chunk_index별 고정 점수로 다시 정렬하는 가짜 Re-ranker (CohereRerank와 같은 출력 형식)."""

    scores: dict[int, float]
    top_n: int = 4
    calls: list = []
    boost: dict[str, dict[int, float]] = {}  # 질의에 키워드가 있으면 해당 청크 점수를 덮어씀

    def score(self, query: str, chunk: int) -> float:
        for keyword, overrides in self.boost.items():
            if keyword in query and chunk in overrides:
                return overrides[chunk]
        return self.scores.get(chunk, 0.0)

    def rerank(self, documents, query, *, top_n=-1, **kwargs):
        """CohereRerank.rerank()와 같은 형식: [{"index", "relevance_score"}] 점수 내림차순."""
        self.calls.append((query, len(documents)))
        results = sorted(({"index": i, "relevance_score": self.score(query, d.metadata["chunk_index"])}
                          for i, d in enumerate(documents)), key=lambda r: -r["relevance_score"])
        return results if top_n is None else results[:top_n if top_n > 0 else self.top_n]

    def compress_documents(self, documents, query, callbacks=None):
        self.calls.append((query, len(documents)))
        ranked = sorted(documents, key=lambda d: self.scores.get(d.metadata["chunk_index"], 0.0),
                        reverse=True)[:self.top_n]
        out = []
        for d in ranked:
            copy = Document(d.page_content, metadata=deepcopy(d.metadata))
            copy.metadata["relevance_score"] = self.scores.get(d.metadata["chunk_index"], 0.0)
            out.append(copy)
        return out


class RecordingLLM(FakeListChatModel):
    prompts: list = []

    def _call(self, messages, *args, **kwargs):
        self.prompts.append(messages)
        return super()._call(messages, *args, **kwargs)


class FakeStore:
    def __init__(self, ids):
        self.ids = ids

    def get(self, include=None):
        return {"ids": self.ids}


def write_index(path: Path, docs: list[Document], **meta_override) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    with open(path / "corpus.jsonl", "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps({"id": d.id, "page_content": d.page_content, "metadata": d.metadata,
                                "tokens": rag.tokenize(d.page_content)}, ensure_ascii=False) + "\n")
    meta = {"format_version": rag.BM25_FORMAT_VERSION, "collection": rag.COLLECTION_NAME,
            "doc_count": len(docs), "bm25_params": {"k1": 1.5, "b": 0.75},
            "tokenizer": {"keep_tags": list(rag.KEEP_TAGS), "article_token": rag.ARTICLE_REF.pattern}}
    meta.update(meta_override)
    (path / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return path


# 1차 검색에서 #61은 낮은 순위지만 Re-ranker는 가장 관련 있다고 판단하는 상황
BM25_DOCS = [doc(5), doc(7), doc(61)]
VECTOR_DOCS = [doc(7), doc(9), doc(11), doc(13)]
SCORES = {61: 0.95, 9: 0.60, 7: 0.30, 5: 0.10, 11: 0.05, 13: 0.01}


def first_stage(**kw):
    return rag.build_ensemble(FakeRetriever(docs=BM25_DOCS, queries=[]),
                              FakeRetriever(docs=VECTOR_DOCS, queries=[]), **kw)


# ---------------------------------------------------------------------------
# 단위 테스트: Re-ranking 검색기
# ---------------------------------------------------------------------------
def test_build_reranker_uses_multilingual_model(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "dummy-key-for-construction")  # 네트워크 호출 없음
    reranker = rag.build_reranker(top_n=3)
    assert reranker.model == "rerank-multilingual-v3.0"
    assert reranker.top_n == 3


def test_rerank_retriever_reorders_all_candidates():
    reranker = FakeReranker(scores=SCORES, top_n=3, calls=[])
    result = rag.build_rerank_retriever(first_stage(), reranker).invoke("특허료 납부 시기")

    assert [d.metadata["chunk_index"] for d in result] == [61, 9, 7]
    assert [d.metadata["relevance_score"] for d in result] == [0.95, 0.60, 0.30]
    # 1차 검색 합집합(중복 #7 제거 → 6개) 전체가 Re-ranker로 넘어감
    assert reranker.calls == [("특허료 납부 시기", 6)]


def test_rerank_retriever_skips_reranker_when_no_candidates():
    reranker = FakeReranker(scores={}, calls=[])
    empty = rag.build_ensemble(FakeRetriever(docs=[]), FakeRetriever(docs=[]))
    assert rag.build_rerank_retriever(empty, reranker).invoke("q") == []
    assert reranker.calls == []  # 빈 후보로 API를 호출하지 않음


@pytest.mark.parametrize("w", [-0.1, 1.1])
def test_build_ensemble_rejects_bad_weight(w):
    with pytest.raises(ValueError):
        rag.build_ensemble(FakeRetriever(docs=[]), FakeRetriever(docs=[]), w)


def test_compare_rerank_reports_first_stage_rank():
    result = rag.compare_rerank("q", first_stage(), FakeReranker(scores=SCORES, top_n=2, calls=[]), k=2)
    assert result["candidates"] == 6
    assert [d.metadata["chunk_index"] for d in result["first_stage"]] == [7, 9]  # RRF 순서
    reranked = result["reranked"]
    assert [d.metadata["chunk_index"] for d in reranked] == [61, 9]
    assert reranked[0].metadata["first_rank"] > 2  # 1차 Top-2 밖에서 끌어올림


def test_print_comparison(capsys):
    result = rag.compare_rerank("q", first_stage(), FakeReranker(scores=SCORES, top_n=2, calls=[]), k=2)
    rag.print_comparison("q", result)
    out = capsys.readouterr().out
    assert "[1차 검색 후보] 6개" in out
    assert "score=0.950" in out and "rerank-multilingual-v3.0" in out


# ---------------------------------------------------------------------------
# 단위 테스트: 체인 / 출력 / 대화형 / CLI 인자
# ---------------------------------------------------------------------------
def test_chain_uses_reranked_docs_as_context():
    retriever = rag.build_rerank_retriever(first_stage(), FakeReranker(scores=SCORES, top_n=2, calls=[]))
    llm = RecordingLLM(responses=["제79조에 따라 납부합니다."])
    result = rag.ask(rag.build_chain(retriever, llm), "  특허료는 언제 내나요?  ")

    assert result["question"] == "특허료는 언제 내나요?"
    assert [d.metadata["chunk_index"] for d in result["docs"]] == [61, 9]
    assert result["answer"] == "제79조에 따라 납부합니다."
    system, human = llm.prompts[-1]
    assert "[출처: 특허법.pdf #61]" in system.content and "#5]" not in system.content
    assert human.content == "특허료는 언제 내나요?"


def test_ask_rejects_empty_question():
    chain = rag.build_chain(FakeRetriever(docs=[doc(1)]), FakeListChatModel(responses=["x"]))
    with pytest.raises(ValueError):
        rag.ask(chain, "   ")


def test_print_result_shows_relevance_score(capsys):
    scored = doc(61)
    scored.metadata["relevance_score"] = 0.9512
    rag.print_result({"answer": " 답변 ", "docs": [scored, doc(9)]})
    out = capsys.readouterr().out
    assert "#61 score=0.951 |" in out and "#9 |" in out


def test_repl_handles_blank_errors_and_exit(capsys):
    inputs = iter(["", "질문1", "exit", "안 읽힘"])
    calls = []

    class FailingChain:
        def invoke(self, q):
            calls.append(q)
            raise RuntimeError("API 오류")

    rag.repl(FailingChain(), input_fn=lambda _: next(inputs))
    assert calls == ["질문1"]
    assert "[오류] RuntimeError: API 오류" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["-w", "1.5", "q"], ["-k", "5", "--fetch-k", "4", "q"],
                                  ["-k", "0", "q"], ["--compare"]])
def test_main_rejects_bad_arguments(argv, monkeypatch):
    def fail():
        raise AssertionError("인자 오류는 인덱스 로드 전에 걸러져야 함")
    monkeypatch.setattr(rag, "load_env", fail)
    with pytest.raises(SystemExit) as e:
        rag.main(argv)
    assert e.value.code == 2


# ---------------------------------------------------------------------------
# 단위 테스트: BM25 인덱스 로드·검증
# ---------------------------------------------------------------------------
DOCS = [doc(17, "제29조(특허요건) ① 산업상 이용할 수 있는 발명은 특허를 받을 수 있다."),
        doc(73, "제88조(특허권의 존속기간) 특허권의 존속기간은 20년으로 한다."),
        doc(124, "제136조(정정심판) 특허권자는 정정심판을 청구할 수 있다.")]


def test_load_bm25_retriever(tmp_path):
    retriever = rag.load_bm25_retriever(write_index(tmp_path / "bm25", DOCS), k=2)
    assert retriever.k == 2
    assert retriever.invoke("정정심판")[0].metadata["chunk_index"] == 124


def test_load_bm25_retriever_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="bm25_indexing"):
        rag.load_bm25_retriever(tmp_path)


@pytest.mark.parametrize("override, match", [
    ({"tokenizer": {"keep_tags": ["NNG"], "article_token": "x"}}, "토크나이저"),
    ({"collection": "other"}, "컬렉션"),
    ({"doc_count": 5}, "손상"),
])
def test_load_bm25_retriever_validates_meta(tmp_path, override, match):
    with pytest.raises(ValueError, match=match):
        rag.load_bm25_retriever(write_index(tmp_path / "bm25", DOCS, **override))


def test_check_same_corpus(tmp_path):
    bm25 = rag.load_bm25_retriever(write_index(tmp_path / "bm25", DOCS))
    rag.check_same_corpus(FakeStore([d.id for d in DOCS]), bm25)
    with pytest.raises(ValueError, match="다시 만드세요"):
        rag.check_same_corpus(FakeStore([d.id for d in DOCS[:2]]), bm25)


# ---------------------------------------------------------------------------
# 단위 테스트: 평가 스크립트
# ---------------------------------------------------------------------------
def test_eval_metrics_and_rerank_simulation():
    assert ev.first_hit_rank([doc(5), doc(61)], {61}, 4) == 2
    assert ev.first_hit_rank([doc(5), doc(61)], {61}, 1) is None
    assert ev.summarize({"all": [1, 2, None, None]})["all"] == (0.5, 0.375)

    fuse = first_stage().weighted_reciprocal_rank
    cached = [{"type": "natural", "gold": {61}, "bm25": BM25_DOCS, "vector": VECTOR_DOCS, "scores": SCORES}]
    report = ev.evaluate(cached, fuse, fetch_ks=(2, 3), k=1)
    assert report["벡터만"]["all"] == (0.0, 0.0)
    assert report["Hybrid+Rerank (후보 2+2)"]["all"] == (0.0, 0.0)  # #61이 후보에 없음
    assert report["Hybrid (후보 3+3)"]["all"] == (0.0, 0.0)
    assert report["Hybrid+Rerank (후보 3+3)"]["natural"] == (1.0, 1.0)  # 후보에 들면 1위로


# ---------------------------------------------------------------------------
# 단위 테스트: 질문 변환 (--transform)
# ---------------------------------------------------------------------------
from langchain_core.runnables import RunnableLambda  # noqa: E402

import evaluate_transform as et  # noqa: E402

Q = "논문으로 먼저 발표한 발명도 특허를 받을 수 있나요?"
REWRITE = rag.QueryPlan(technique="rewrite", reason="일상어", queries=["공지 예외 신규성 상실"])


class QueryRetriever(BaseRetriever):
    """질의별로 다른 결과를 돌려주는 가짜 검색기."""

    results: dict[str, list[Document]]
    queries: list[str] = []

    def _get_relevant_documents(self, query, *, run_manager=None):
        self.queries.append(query)
        return self.results.get(query, [])


def transform_setup(plan):
    # 원 질문으로는 #17, #23만 찾고, 변환 질의로만 #20(제30조)을 찾음
    bm25 = QueryRetriever(results={Q: [doc(17), doc(23)], "공지 예외 신규성 상실": [doc(20), doc(17)]}, queries=[])
    vector = QueryRetriever(results={Q: [doc(23)], "공지 예외 신규성 상실": [doc(20)]}, queries=[])
    reranker = FakeReranker(scores={17: 0.9, 23: 0.8, 20: 0.1}, boost={"공지 예외": {20: 0.99}}, calls=[])
    transformer = RunnableLambda(lambda q: plan)
    return transformer, rag.build_ensemble(bm25, vector), reranker, bm25


def test_query_plan_rejects_unknown_technique():
    with pytest.raises(ValueError):
        rag.QueryPlan(technique="magic", reason="x", queries=[])


def test_transform_prompt_lists_all_techniques():
    messages = rag.TRANSFORM_PROMPT.invoke({"question": Q}).to_messages()
    for tech in ("none", "rewrite", "multi_query", "hyde", "step_back", "decomposition"):
        assert tech in messages[0].content
    assert messages[1].content == Q


def test_search_queries_keeps_original_first_and_dedups():
    plan = rag.QueryPlan(technique="multi_query", reason="r",
                         queries=[" a ", "", Q, "b", "c", "d", "e"])
    assert rag.search_queries(Q, plan) == [Q, "a", "b"]  # 앞 4개만 사용 → 공백·빈 값·원 질문 중복 제거
    assert rag.search_queries(Q, rag.QueryPlan(technique="none", reason="r", queries=["무시"])) == [Q]


def test_rerank_query_joins_original_and_transformed():
    assert rag.rerank_query(Q, REWRITE) == f"{Q}\n공지 예외 신규성 상실"
    assert rag.rerank_query(Q, rag.QueryPlan(technique="none", reason="r", queries=[])) == Q


def test_merge_candidates_dedups_in_order():
    merged = rag.merge_candidates([[doc(1), doc(2)], [doc(2), doc(3)], []])
    assert [d.metadata["chunk_index"] for d in merged] == [1, 2, 3]


def test_rerank_docs_scores_without_mutating_input():
    reranker = FakeReranker(scores={1: 0.2, 2: 0.7}, calls=[])
    docs = [doc(1), doc(2)]
    top = rag.rerank_docs(reranker, docs, "q", k=1)
    assert [(d.metadata["chunk_index"], d.metadata["relevance_score"]) for d in top] == [(2, 0.7)]
    assert "relevance_score" not in docs[1].metadata
    assert rag.rerank_docs(reranker, [], "q") == [] and len(reranker.calls) == 1  # 빈 후보는 호출 안 함


def test_transform_finds_chunk_missed_by_original_question():
    transformer, stage1, reranker, bm25 = transform_setup(REWRITE)
    result = rag.transform_and_retrieve(Q, transformer, stage1, reranker, k=2)

    assert result["plan"] is REWRITE
    assert result["queries"] == [Q, "공지 예외 신규성 상실"]
    assert set(bm25.queries) == {Q, "공지 예외 신규성 상실"}  # 질의마다 1차 검색
    assert result["candidates"] == 3                        # #17, #23, #20 합집합
    assert [d.metadata["chunk_index"] for d in result["docs"]] == [20, 17]
    assert reranker.calls == [(f"{Q}\n공지 예외 신규성 상실", 3)]  # Re-ranking은 1회


def test_transform_none_equals_plain_rerank():
    plan = rag.QueryPlan(technique="none", reason="명확", queries=[])
    transformer, stage1, reranker, _ = transform_setup(plan)
    result = rag.transform_and_retrieve(Q, transformer, stage1, reranker, k=2)
    plain = rag.build_rerank_retriever(stage1, FakeReranker(scores=reranker.scores, top_n=2, calls=[])).invoke(Q)
    assert [d.metadata["chunk_index"] for d in result["docs"]] == [d.metadata["chunk_index"] for d in plain]
    assert reranker.calls == [(Q, 2)]


def test_transform_chain_answers_original_question():
    transformer, stage1, reranker, _ = transform_setup(REWRITE)
    llm = RecordingLLM(responses=["제30조에 따라 가능합니다."], prompts=[])
    result = rag.ask(rag.build_transform_chain(transformer, stage1, reranker, llm, k=2), f"  {Q} ")

    assert result["plan"].technique == "rewrite"
    assert result["docs"][0].metadata["chunk_index"] == 20
    assert result["answer"] == "제30조에 따라 가능합니다."
    system, human = llm.prompts[-1]
    assert human.content == Q                       # 답변은 원 질문으로 생성
    assert "[출처: 특허법.pdf #20]" in system.content


def test_print_result_and_comparison_show_transform(capsys):
    transformer, stage1, reranker, _ = transform_setup(REWRITE)
    result = rag.compare_rerank(Q, stage1, reranker, k=2)
    result["transform"] = rag.transform_and_retrieve(Q, transformer, stage1, reranker, k=2)
    rag.print_comparison(Q, result)
    rag.print_result({"answer": "답", "plan": REWRITE, "docs": []})
    out = capsys.readouterr().out
    assert "[질문 변환] rewrite - 일상어" in out and "· 공지 예외 신규성 상실" in out
    assert "#20  score=0.990 (새로 진입)" in out


def test_main_accepts_transform_flag(monkeypatch):
    def stop():
        raise SystemExit(0)
    monkeypatch.setattr(rag, "load_env", stop)  # 인자 파싱만 확인
    with pytest.raises(SystemExit) as e:
        rag.main(["-t", "--compare", "질문"])
    assert e.value.code == 0


def test_eval_transform_helpers(capsys):
    docs = [doc(1), doc(2), doc(3)]
    assert [d.metadata["chunk_index"] for d in et.top_by_score(docs, {1: 0.1, 2: 0.9, 3: 0.5}, 2)] == [2, 3]
    rows = [{"type": "natural", "question": Q, "plan": REWRITE, "pool": (16, 25), "before": None, "after": 1},
            {"type": "keyword", "question": "제133조", "plan": rag.QueryPlan(technique="none", reason="r", queries=[]),
             "pool": (12, 12), "before": 1, "after": 1}]
    et.report(rows, k=4)  # article 유형이 없어도 동작
    out = capsys.readouterr().out
    assert "natural   |    1 |    0%   0.000 |  100%   1.000" in out
    assert "없음 → 1위" in out and "rewrite" in out


def test_throttle_spaces_calls(monkeypatch):
    sleeps, now = [], [100.0]
    monkeypatch.setattr(et.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(et.time, "sleep", lambda s: sleeps.append(s))
    throttle = et.Throttle(6.5)
    throttle.wait()
    now[0] += 2.0
    throttle.wait()
    assert sleeps == [pytest.approx(4.5)]


def test_article_set_is_deterministic():
    docs = [doc(0, "제1조(목적) …\n제2조(정의) …"), doc(1, "제3조(능력) …")]
    assert ev.article_set(docs, n=2) == ev.article_set(docs, n=2)
    assert all(q[1].endswith("의 내용은?") for q in ev.article_set(docs, n=2))


# ---------------------------------------------------------------------------
# 통합 테스트: 실제 인덱스 + API
# ---------------------------------------------------------------------------
load_dotenv(rag.ENV_FILE)
HAS_INDEXES = (rag.VECTORDB_DIR / "chroma.sqlite3").exists() and (rag.BM25_DIR / "meta.json").exists()
HAS_KEYS = bool(os.getenv("COHERE_API_KEY") and os.getenv("GROQ_API_KEY"))
needs_index = pytest.mark.skipif(not HAS_INDEXES, reason="vectordb/ 또는 bm25/가 없습니다")
needs_api = pytest.mark.skipif(not (HAS_INDEXES and HAS_KEYS), reason="인덱스 또는 API 키가 없습니다")


@pytest.fixture(scope="module")
def pipeline():
    rag.load_env()
    store = rag.load_vectorstore()
    bm25 = rag.load_bm25_retriever()
    rag.check_same_corpus(store, bm25)
    stage1 = rag.build_ensemble(bm25, rag.build_vector_retriever(store))
    return stage1, rag.build_rerank_retriever(stage1, rag.build_reranker())


@needs_index
def test_stored_tokens_match_query_tokenizer():
    lines = (rag.BM25_DIR / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
    for line in lines[::20]:
        r = json.loads(line)
        assert rag.tokenize(r["page_content"]) == r["tokens"], r["id"]


@needs_api
@pytest.mark.parametrize("question, gold", [
    ("특허를 받을 수 있는 조건은 ?", 17),            # 제29조(특허요건)
    ("특허 등록하려면 돈을 언제 내야 하나요?", 61),  # 제79조(특허료)
    ("제133조", 117),                                # 제133조(특허의 무효심판)
])
def test_rerank_search_finds_article(pipeline, question, gold):
    _, retriever = pipeline
    docs = ev.with_retry(retriever.invoke, question)
    assert len(docs) == rag.TOP_K
    assert gold in [d.metadata["chunk_index"] for d in docs]
    scores = [d.metadata["relevance_score"] for d in docs]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 1 for s in scores)


@needs_api
def test_first_stage_returns_wide_candidate_pool(pipeline):
    stage1, _ = pipeline
    docs = ev.with_retry(stage1.invoke, "특허권의 존속기간은?")
    assert rag.FETCH_K < len(docs) <= 2 * rag.FETCH_K  # BM25 + 벡터 합집합
    assert len({d.page_content for d in docs}) == len(docs)


def run_cli(*args, timeout=180):
    return subprocess.run(
        [sys.executable, "rerank_rag.py", *args],
        cwd=Path(__file__).parent, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=timeout,
    )


@pytest.mark.skipif(not os.getenv("GROQ_API_KEY"), reason="GROQ_API_KEY가 없습니다")
@pytest.mark.parametrize("question", ["제133조", "회사 일을 하다 만든 발명은 누가 특허를 받나요?"])
def test_transformer_returns_valid_plan(question):
    """실제 Groq LLM의 구조화 출력이 QueryPlan 규칙을 지키는지 확인 (Cohere 호출 없음)."""
    plan = rag.build_transformer(rag.build_llm(max_retries=4)).invoke(question)
    assert isinstance(plan, rag.QueryPlan)
    assert plan.reason.strip()
    if plan.technique != "none":
        assert plan.queries and all(q.strip() for q in plan.queries)


@needs_api
def test_cli_transform_end_to_end():
    proc = run_cli("--transform", "미성년자도 특허출원을 할 수 있나요?")
    assert proc.returncode == 0, proc.stderr
    assert "[질문 변환]" in proc.stdout and "[답변]" in proc.stdout
    assert "특허법.pdf #1 score=" in proc.stdout  # 제3조(미성년자 등의 행위능력)


@needs_api
def test_cli_end_to_end_with_llm():
    proc = run_cli("특허를 받을 수 있는 조건은 ?")
    assert proc.returncode == 0, proc.stderr
    assert "[답변]" in proc.stdout and "제29조" in proc.stdout
    assert "특허법.pdf #17 score=" in proc.stdout


@needs_api
def test_cli_compare_mode():
    proc = run_cli("--compare", "남의 특허를 허락 없이 쓰면 어떤 처벌을 받나요?", timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "[1차 검색 Top 4]" in proc.stdout and "[Re-ranking Top 4] (rerank-multilingual-v3.0)" in proc.stdout
    first, reranked = proc.stdout.split("[Re-ranking Top 4]")
    assert "#187" not in first.split("[1차 검색 Top 4]")[1]  # 1차 Top-4 밖의 제225조(침해죄)를
    assert "1. #187" in reranked                            # Re-ranking이 1위로 올림
    assert "[답변]" not in proc.stdout  # LLM 호출 없음
