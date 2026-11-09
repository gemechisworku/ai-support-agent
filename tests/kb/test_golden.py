"""T-KB-06 · KB-R8: golden retrieval questions.

- integration (CI): every `must_pass_ci` question hits with the offline HashEmbedder and is confident
  at `HASH_MIN_SIMILARITY`.
- eval (manual, needs the llama.cpp embeddings server): the full set with OpenAICompatEmbedder reaches
  hit@4 ≥ 0.90; hit@4, MRR and the similarity range are printed to help tune `KB_MIN_SIMILARITY`.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.orm import Session

from supportagent.contracts.kb import PolicyQuery
from supportagent.kb.embed import Embedder, EmbedderError, HashEmbedder, OpenAICompatEmbedder
from supportagent.kb.ingest import ingest
from supportagent.kb.retrieval import HASH_MIN_SIMILARITY, search

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "policies"
GOLDEN: list[dict[str, Any]] = yaml.safe_load((Path(__file__).parent / "golden_questions.yaml").read_text())


def test_golden_set_shape() -> None:
    assert len(GOLDEN) >= 30
    doc_ids = {p.stem for p in CORPUS.glob("*.md")}
    for item in GOLDEN:
        assert set(item) == {"question", "product", "expected_doc_ids", "must_pass_ci"}
        assert set(item["expected_doc_ids"]) <= doc_ids, item["question"]
    assert sum(item["must_pass_ci"] for item in GOLDEN) >= 20


def _evaluate(session: Session, embedder: Embedder, min_similarity: float | None) -> list[dict[str, Any]]:
    rows = []
    for item in GOLDEN:
        result = search(
            PolicyQuery(query=item["question"], product=item["product"]),
            session=session,
            embedder=embedder,
            min_similarity=min_similarity,
        )
        ids = [c.doc_id for c in result.chunks]
        rank = next((i + 1 for i, d in enumerate(ids) if d in item["expected_doc_ids"]), None)
        rows.append(
            {**item, "ids": ids, "rank": rank, "confident": result.confident, "sim": result.best_similarity}
        )
    return rows


def _report(rows: list[dict[str, Any]]) -> tuple[float, float]:
    hit = sum(r["rank"] is not None for r in rows) / len(rows)
    mrr = sum(1 / r["rank"] for r in rows if r["rank"]) / len(rows)
    lo, hi = min(r["sim"] for r in rows), max(r["sim"] for r in rows)
    print(f"\nhit@4={hit:.3f} MRR={mrr:.3f} sim=[{lo:.3f}, {hi:.3f}]")
    for r in rows:
        if r["rank"] is None:
            print(f"  MISS {r['question']!r} -> {r['ids']}")
    return hit, mrr


@pytest.mark.integration
def test_must_pass_questions_with_hash_embedder(kb_db: Session) -> None:
    embedder = HashEmbedder()
    ingest(CORPUS, kb_db, embedder)
    rows = _evaluate(kb_db, embedder, HASH_MIN_SIMILARITY)
    _report(rows)
    failures = [
        (r["question"], r["ids"], r["confident"])
        for r in rows
        if r["must_pass_ci"] and not (r["rank"] and r["confident"])
    ]
    assert failures == []


@pytest.mark.eval
def test_full_golden_set_with_llamacpp(kb_db: Session, settings: Any) -> None:
    embedder = OpenAICompatEmbedder(settings.models.embed_url, settings.models.embed_model)
    try:
        embedder.embed_query("ping")
    except EmbedderError as exc:
        pytest.skip(f"embeddings server not available: {exc}")
    ingest(CORPUS, kb_db, embedder)
    rows = _evaluate(kb_db, embedder, None)
    hit, _ = _report(rows)
    assert hit >= 0.90
