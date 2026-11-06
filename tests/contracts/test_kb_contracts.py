"""T-KB-02 · KB-R6/R7 contracts: PolicyQuery, PolicyChunk, PolicySearchResult."""

from datetime import date

import pytest
from pydantic import ValidationError

from supportagent.contracts.kb import PRODUCT_CODES, PolicyChunk, PolicyQuery, PolicySearchResult


def test_query_defaults() -> None:
    q = PolicyQuery(query="What happens if I pay late?")
    assert (q.product, q.audience, q.as_of, q.top_k) == (None, "customer", None, 4)


@pytest.mark.parametrize("top_k", [1, 4, 8])
def test_top_k_in_range(top_k: int) -> None:
    assert PolicyQuery(query="x", top_k=top_k).top_k == top_k


@pytest.mark.parametrize("top_k", [0, 9, -1])
def test_top_k_out_of_range(top_k: int) -> None:
    with pytest.raises(ValidationError):
        PolicyQuery(query="x", top_k=top_k)


@pytest.mark.parametrize("product", [*PRODUCT_CODES, None])
def test_allowed_products(product: str | None) -> None:
    assert PolicyQuery(query="x", product=product).product == product  # type: ignore[arg-type]


@pytest.mark.parametrize("product", ["all", "auto", "CREDIT_CARD", ""])
def test_rejected_products(product: str) -> None:
    with pytest.raises(ValidationError):
        PolicyQuery(query="x", product=product)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [{"query": ""}, {"query": "x" * 501}, {"query": "x", "audience": "staff"}])
def test_query_validation(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PolicyQuery.model_validate(bad)


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        PolicyQuery.model_validate({"query": "x", "customer_id": 1})


def test_chunk_citation_and_result_round_trip() -> None:
    chunk = PolicyChunk(
        doc_id="late-fees-penalty-interest",
        version="1.0",
        title="Late fees",
        section="Late fees > Auto loan",
        product="all",
        text="A late fee of 5%...",
        rrf_score=0.0328,
        similarity=0.71,
    )
    assert chunk.citation == "late-fees-penalty-interest@1.0#Late fees > Auto loan"
    result = PolicySearchResult(chunks=[chunk], confident=True, best_similarity=0.71)
    assert PolicySearchResult.model_validate_json(result.model_dump_json()) == result
    q = PolicyQuery(query="x", as_of=date(2026, 11, 9))
    assert PolicyQuery.model_validate_json(q.model_dump_json()) == q
