"""T-KB-06 · KB-R6/KB-R7: hybrid retrieval against the ingested corpus (HashEmbedder, kb_test)."""

import shutil
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from supportagent.common.clock import freeze_clock
from supportagent.contracts.kb import PolicyQuery
from supportagent.kb.embed import HashEmbedder
from supportagent.kb.ingest import ingest
from supportagent.kb.retrieval import HASH_MIN_SIMILARITY, search

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "policies"
EMBEDDER = HashEmbedder()


@pytest.fixture
def corpus(kb_db: Session) -> Session:
    ingest(CORPUS, kb_db, EMBEDDER)
    return kb_db


def _search(session: Session, question: str, **kw: object) -> object:
    return search(
        PolicyQuery(query=question, **kw),  # type: ignore[arg-type]
        session=session,
        embedder=EMBEDDER,
        min_similarity=HASH_MIN_SIMILARITY,
    )


def test_spec_example_auto_early_payoff(corpus: Session) -> None:
    result = search(
        PolicyQuery(query="Can I pay my car loan off early?", product="AUTO"),
        session=corpus,
        embedder=EMBEDDER,
        min_similarity=HASH_MIN_SIMILARITY,
    )
    assert 1 <= len(result.chunks) <= 4
    assert {c.product for c in result.chunks} <= {"AUTO", "all"}
    assert result.chunks[0].doc_id in {"early-repayment-payoff", "product-auto-loan"}
    assert result.confident
    top = result.chunks[0]
    assert top.version == "1.0" and top.section and top.text and top.title
    assert top.similarity is not None and top.rrf_score > 0
    assert [c.rrf_score for c in result.chunks] == sorted((c.rrf_score for c in result.chunks), reverse=True)


def test_off_topic_is_not_confident(corpus: Session) -> None:
    result = search(
        PolicyQuery(query="what's the weather in Paris"),
        session=corpus,
        embedder=EMBEDDER,
        min_similarity=HASH_MIN_SIMILARITY,
    )
    assert result.confident is False


def test_default_threshold_comes_from_settings(corpus: Session, settings: object) -> None:
    # KB_MIN_SIMILARITY defaults to 0.55 (tuned for nomic-embed-text); the lexical HashEmbedder never
    # reaches it, so with the default threshold even a good match is reported as not confident.
    result = search(
        PolicyQuery(query="How much is the late fee on my auto loan?"), session=corpus, embedder=EMBEDDER
    )
    assert result.chunks and result.confident is False


@pytest.mark.parametrize("product", ["PERSONAL", "AUTO", "MORTGAGE", "SME_WC", "SALARY_ADV"])
def test_product_filter(corpus: Session, product: str) -> None:
    result = search(
        PolicyQuery(query="grace period and late fee", product=product, top_k=8),  # type: ignore[arg-type]
        session=corpus,
        embedder=EMBEDDER,
        min_similarity=HASH_MIN_SIMILARITY,
    )
    assert result.chunks
    assert {c.product for c in result.chunks} <= {product, "all"}


def test_no_product_searches_every_product(corpus: Session) -> None:
    result = search(
        PolicyQuery(query="late fee grace period product terms", top_k=8),
        session=corpus,
        embedder=EMBEDDER,
        min_similarity=HASH_MIN_SIMILARITY,
    )
    assert len({c.product for c in result.chunks} - {"all"}) >= 2


@pytest.mark.parametrize("top_k", [1, 4, 8])
def test_top_k(corpus: Session, top_k: int) -> None:
    result = search(
        PolicyQuery(query="late fee", top_k=top_k),
        session=corpus,
        embedder=EMBEDDER,
        min_similarity=HASH_MIN_SIMILARITY,
    )
    assert len(result.chunks) == top_k


def test_customer_audience_never_sees_internal_docs(corpus: Session, settings: object) -> None:
    question = "calling window frequency limits verification disclosures"
    customer = search(PolicyQuery(query=question, top_k=8), session=corpus, embedder=EMBEDDER)
    internal = search(
        PolicyQuery(query=question, audience="internal", top_k=8), session=corpus, embedder=EMBEDDER
    )
    internal_ids = {"contact-calling-rules", "identity-verification-privacy", "required-disclosures"}
    assert not {c.doc_id for c in customer.chunks} & internal_ids
    assert {c.doc_id for c in internal.chunks} & internal_ids


def test_version_effective_at_as_of(kb_db: Session, tmp_path: Path, settings: object) -> None:
    ingest(CORPUS, kb_db, EMBEDDER)
    v11 = tmp_path / "late-fees-penalty-interest.md"
    shutil.copy(CORPUS / v11.name, v11)
    v11.write_text(
        v11.read_text()
        .replace('version: "1.0"', 'version: "1.1"')
        .replace("effective_date: 2026-01-01", "effective_date: 2027-01-01")
        .replace(
            "## Penalty interest",
            "## Penalty interest\nVersion one point one wording about penalty interest.",
        )
    )
    ingest(tmp_path, kb_db, EMBEDDER)

    def versions(as_of: date | None) -> set[str]:
        result = search(
            PolicyQuery(query="penalty interest late fees", as_of=as_of, top_k=8),
            session=kb_db,
            embedder=EMBEDDER,
        )
        return {c.version for c in result.chunks if c.doc_id == "late-fees-penalty-interest"}

    assert versions(date(2026, 11, 9)) == {"1.0"}  # v1.1 is in the future: ignored
    assert versions(date(2027, 1, 1)) == {"1.1"}  # and once effective it replaces v1.0
    with freeze_clock("2026-11-09T14:00:00Z"):
        assert versions(None) == {"1.0"}  # as_of defaults to today


def test_only_chunks_from_the_query_embedder(corpus: Session, settings: object) -> None:
    class OtherEmbedder(HashEmbedder):
        name = "other-v1"

    result = search(PolicyQuery(query="late fee"), session=corpus, embedder=OtherEmbedder())
    assert result.chunks == [] and result.confident is False and result.best_similarity == 0.0


def test_citation_format(corpus: Session, settings: object) -> None:
    chunk = search(PolicyQuery(query="promise to pay rules"), session=corpus, embedder=EMBEDDER).chunks[0]
    assert chunk.citation == f"{chunk.doc_id}@{chunk.version}#{chunk.section}"
