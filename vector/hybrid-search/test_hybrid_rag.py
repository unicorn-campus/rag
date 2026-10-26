"""hybrid_rag.py 테스트.

- 단위 테스트: 토크나이저, 가중 RRF 결합·Top-K, 인덱스 검증, 체인, 대화형 모드
  (가짜 LLM/Retriever, API 호출 없음)
- 통합 테스트: 공용 vectordb + bm25 인덱스 검색, Groq LLM 답변
  (COHERE_API_KEY, GROQ_API_KEY, vectordb/, bm25/ 필요)

실행: pytest -v
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.retrievers import BaseRetriever

import evaluate_weights as ev
import hybrid_rag as rag


def doc(i: int, text: str | None = None) -> Document:
    return Document(id=f"특허법.pdf::{i:05d}", page_content=text or f"청크 {i}",
                    metadata={"source": "특허법.pdf", "chunk_index": i})


class FakeRetriever(BaseRetriever):
    docs: list[Document]
    queries: list[str] = []

    def _get_relevant_documents(self, query, *, run_manager=None):
        self.queries.append(query)
        return self.docs


class RecordingLLM(FakeListChatModel):
    """LLM에 전달된 프롬프트 메시지를 기록하는 가짜 모델."""

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


# ---------------------------------------------------------------------------
# 단위 테스트: 토크나이저
# ---------------------------------------------------------------------------
def test_tokenize_query():
    assert rag.tokenize("특허법 제42조 출원 방법")[0] == "제42조"
    assert {"특허", "출원", "방법"} <= set(rag.tokenize("특허법 제42조 출원 방법"))
    assert "를" not in rag.tokenize("특허를 받을 수 있는 조건은?")


# ---------------------------------------------------------------------------
# 단위 테스트: 가중 RRF 결합
# ---------------------------------------------------------------------------
def test_hybrid_fuses_dedups_and_cuts_to_top_k():
    bm25 = FakeRetriever(docs=[doc(1), doc(2), doc(3)])
    vector = FakeRetriever(docs=[doc(2), doc(4), doc(5)])
    result = rag.build_hybrid_retriever(bm25, vector, bm25_weight=0.5, k=3).invoke("질문")

    ids = [d.metadata["chunk_index"] for d in result]
    assert ids[0] == 2           # 양쪽에서 검색된 청크가 1위
    assert len(ids) == 3 == len(set(ids))  # 중복 제거 + Top-K
    assert bm25.queries[-1] == vector.queries[-1] == "질문"


def test_weights_change_ranking():
    bm25 = FakeRetriever(docs=[doc(1), doc(9)])
    vector = FakeRetriever(docs=[doc(2), doc(8)])
    top = lambda w: rag.build_hybrid_retriever(bm25, vector, w, k=1).invoke("q")[0].metadata["chunk_index"]
    assert top(0.3) == 2  # 벡터 가중치가 크면 벡터 1위
    assert top(0.7) == 1  # BM25 가중치가 크면 BM25 1위


def test_rrf_score_matches_formula():
    ensemble = rag.build_ensemble(FakeRetriever(docs=[]), FakeRetriever(docs=[]), 0.3)
    assert ensemble.weights == pytest.approx([0.3, 0.7]) and ensemble.c == rag.RRF_C
    # doc A: BM25 1위 + 벡터 3위, doc B: 벡터 1위
    a, b = doc(1), doc(2)
    fused = ensemble.weighted_reciprocal_rank([[a], [b, doc(3), a]])
    score_a = 0.3 / (1 + 60) + 0.7 / (3 + 60)
    score_b = 0.7 / (1 + 60)
    assert (fused[0] is a) == (score_a > score_b)


@pytest.mark.parametrize("w", [-0.1, 1.1])
def test_build_ensemble_rejects_bad_weight(w):
    with pytest.raises(ValueError):
        rag.build_ensemble(FakeRetriever(docs=[]), FakeRetriever(docs=[]), w)


def test_compare_retrievers_calls_each_retriever_once():
    bm25 = FakeRetriever(docs=[doc(1), doc(2)], queries=[])
    vector = FakeRetriever(docs=[doc(2), doc(3)], queries=[])
    result = rag.compare_retrievers("q", bm25, vector, 0.3, k=2)
    assert [d.metadata["chunk_index"] for d in result["hybrid"]] == [2, 3]
    assert len(result["bm25"]) == len(result["vector"]) == 2
    assert len(bm25.queries) == len(vector.queries) == 1


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
    assert retriever.invoke("존속기간")[0].id == "특허법.pdf::00073"


def test_load_bm25_retriever_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="bm25_indexing"):
        rag.load_bm25_retriever(tmp_path)


@pytest.mark.parametrize("override, match", [
    ({"tokenizer": {"keep_tags": ["NNG"], "article_token": "x"}}, "토크나이저"),
    ({"collection": "other"}, "컬렉션"),
    ({"format_version": 99}, "형식"),
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
# 단위 테스트: 체인 / 대화형 / CLI 인자
# ---------------------------------------------------------------------------
def test_chain_returns_answer_and_docs_and_injects_context():
    retriever = rag.build_hybrid_retriever(FakeRetriever(docs=DOCS[:2]), FakeRetriever(docs=DOCS[1:]), k=2)
    llm = RecordingLLM(responses=["제88조에 따라 20년입니다."])
    result = rag.ask(rag.build_chain(retriever, llm), "  존속기간은?  ")

    assert result["question"] == "존속기간은?"
    assert result["docs"][0].metadata["chunk_index"] == 73  # 양쪽에서 검색된 청크
    assert len(result["docs"]) == 2
    assert result["answer"] == "제88조에 따라 20년입니다."
    system, human = llm.prompts[-1]
    assert "[출처: 특허법.pdf #73]" in system.content
    assert human.content == "존속기간은?"


def test_ask_rejects_empty_question():
    chain = rag.build_chain(FakeRetriever(docs=DOCS), FakeListChatModel(responses=["x"]))
    with pytest.raises(ValueError):
        rag.ask(chain, "   ")


def test_repl_handles_blank_errors_and_exit(capsys):
    inputs = iter(["", "질문1", "종료", "안 읽힘"])
    calls = []

    class FailingChain:
        def invoke(self, q):
            calls.append(q)
            raise RuntimeError("API 오류")

    rag.repl(FailingChain(), input_fn=lambda _: next(inputs))
    assert calls == ["질문1"]
    assert "[오류] RuntimeError: API 오류" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["-w", "1.5", "q"], ["-k", "5", "--fetch-k", "4", "q"], ["--compare"]])
def test_main_rejects_bad_arguments(argv, monkeypatch):
    monkeypatch.setattr(rag, "load_env", lambda: None)
    monkeypatch.setattr(rag, "load_vectorstore", lambda: FakeStore([]))
    monkeypatch.setattr(rag, "load_bm25_retriever", lambda k: FakeRetriever(docs=[]))
    monkeypatch.setattr(rag, "check_same_corpus", lambda s, b: None)
    with pytest.raises(SystemExit) as e:
        rag.main(argv)
    assert e.value.code == 2


def test_eval_metrics():
    docs = [doc(5), doc(17), doc(9)]
    assert ev.first_hit_rank(docs, {17}, 4) == 2
    assert ev.first_hit_rank(docs, {17}, 1) is None
    questions = ev.article_set([doc(0, "제1조(목적) …\n제2조(정의) …"), doc(1, "제3조(능력) …")], n=2)
    assert len(questions) == 2 and all(q[1].endswith("의 내용은?") for q in questions)
    assert questions == ev.article_set([doc(0, "제1조(목적) …\n제2조(정의) …"), doc(1, "제3조(능력) …")], n=2)


# ---------------------------------------------------------------------------
# 통합 테스트: 실제 인덱스 + API
# ---------------------------------------------------------------------------
load_dotenv(rag.ENV_FILE)
HAS_INDEXES = (rag.VECTORDB_DIR / "chroma.sqlite3").exists() and (rag.BM25_DIR / "meta.json").exists()
HAS_KEYS = bool(os.getenv("COHERE_API_KEY") and os.getenv("GROQ_API_KEY"))
needs_index = pytest.mark.skipif(not HAS_INDEXES, reason="vectordb/ 또는 bm25/가 없습니다")
needs_api = pytest.mark.skipif(not (HAS_INDEXES and HAS_KEYS), reason="인덱스 또는 API 키가 없습니다")


@pytest.fixture(scope="module")
def indexes():
    rag.load_env()
    store = rag.load_vectorstore()
    bm25 = rag.load_bm25_retriever()
    return store, bm25, rag.build_vector_retriever(store)


@needs_index
def test_stored_tokens_match_query_tokenizer():
    """bm25_indexing.py와 이 파일의 토크나이저가 같아야 질의·문서 토큰이 맞음."""
    lines = (rag.BM25_DIR / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
    for line in lines[::20]:
        r = json.loads(line)
        assert rag.tokenize(r["page_content"]) == r["tokens"], r["id"]


@needs_api
def test_indexes_share_same_corpus(indexes):
    store, bm25, _ = indexes
    rag.check_same_corpus(store, bm25)
    assert len(bm25.docs) == store._collection.count()


@needs_api
@pytest.mark.parametrize("question, gold", [
    ("특허를 받을 수 있는 조건은 ?", 17),   # 제29조(특허요건)
    ("특허법 제42조 출원 방법", 28),       # 제42조(특허출원)
    ("특허권의 존속기간은?", 73),          # 제88조
])
def test_hybrid_search_finds_article(indexes, question, gold):
    _, bm25, vector = indexes
    docs = rag.build_hybrid_retriever(bm25, vector).invoke(question)
    assert len(docs) == rag.TOP_K
    assert gold in [d.metadata["chunk_index"] for d in docs]


@needs_api
def test_cli_end_to_end_with_llm():
    proc = subprocess.run(
        [sys.executable, "hybrid_rag.py", "특허를 받을 수 있는 조건은 ?"],
        cwd=Path(__file__).parent, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert "[답변]" in proc.stdout and "제29조" in proc.stdout
    assert "특허법.pdf #17" in proc.stdout


@needs_api
def test_cli_compare_mode():
    proc = subprocess.run(
        [sys.executable, "hybrid_rag.py", "--compare", "제133조"],
        cwd=Path(__file__).parent, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    for label in ("[BM25 Top 4]", "[벡터 Top 4]", "[Hybrid (BM25 0.3 : 벡터 0.7) Top 4]"):
        assert label in proc.stdout
    assert "[답변]" not in proc.stdout  # LLM 호출 없음
