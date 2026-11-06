"""Hybrid policy retrieval: vector + full-text, fused with reciprocal rank fusion (KB-R6, KB-R7).

    search(PolicyQuery(query="Can I pay my car loan off early?", product="AUTO"),
           session=kb_session, embedder=get_embedder())  -> PolicySearchResult

1. Scope: per `doc_id`, the version with the latest `effective_date ≤ as_of` (default today), embedded
   by the same embedder as the query; then `product ∈ {query.product, "all"}` (no product = every
   product) and audience (`customer` sees customer docs; `internal` sees both).
2. Candidates: 20 nearest chunks by cosine distance (HNSW, `hnsw.ef_search = 40`) and 20 full-text
   matches ranked by `ts_rank_cd`. The full-text query is `websearch_to_tsquery('english', q)` with its
   terms OR-ed instead of AND-ed: spoken questions are long, and requiring every word recalls nothing.
3. Fusion: RRF with k=60; the top `top_k` chunks are returned in fused order, ties broken by the best
   single rank, then chunk id (deterministic).
4. Confidence: `confident` requires both `best_similarity ≥ min_similarity` (default
   `KB_MIN_SIMILARITY`), where `best_similarity` is the highest cosine similarity among the vector
   candidates, and at least one full-text match (some content word of the question appears in the
   policies). RRF scores are rank-only, so they cannot say "nothing relevant exists"; the similarity
   and the lexical guard can. The guard stops a vector-only near miss ("what's the weather in
   Paris") from being presented as policy.

Similarity scales differ by embedder: `KB_MIN_SIMILARITY=0.55` is the starting value for
nomic-embed-text; the offline `HashEmbedder` scores relevant chunks at roughly 0.13-0.46, so tests that
use it pass `min_similarity=HASH_MIN_SIMILARITY`.
"""

from __future__ import annotations

from collections.abc import Sequence

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from supportagent.common import clock
from supportagent.contracts.kb import PolicyChunk, PolicyQuery, PolicySearchResult
from supportagent.kb.embed import Embedder
from supportagent.kb.models import EMBED_DIM

RRF_K = 60
CANDIDATES = 20
EF_SEARCH = 40
HASH_MIN_SIMILARITY = 0.12  # calibrated on tests/kb/golden_questions.yaml with HashEmbedder

_SCOPE = """
with eff as (
    select distinct on (d.doc_id) d.id, d.product, d.audience
    from documents d
    where d.effective_date <= :as_of and d.embedder = :embedder
    order by d.doc_id, d.effective_date desc, string_to_array(d.version, '.')::int[] desc
),
scoped as (
    select id from eff
    where (cast(:product as text) is null or product = :product or product = 'all')
      and (audience = 'customer' or :audience = 'internal')
)
"""

_VECTOR_SQL = text(
    _SCOPE
    + """
select c.id, 1 - (c.embedding <=> :qv) as similarity
from chunks c join scoped s on s.id = c.document_id
order by c.embedding <=> :qv, c.id
limit :n
"""
).bindparams(bindparam("qv", type_=Vector(EMBED_DIM)))

_FTS_SQL = text(
    _SCOPE
    + """,
q as (select replace(websearch_to_tsquery('english', :q)::text, ' & ', ' | ')::tsquery as q)
select c.id
from chunks c join scoped s on s.id = c.document_id, q
where c.tsv @@ q.q
order by ts_rank_cd(c.tsv, q.q) desc, c.id
limit :n
"""
)

_DETAILS_SQL = text(
    """
select c.id, d.doc_id, d.version, d.title, c.section_path, d.product, c.text,
       1 - (c.embedding <=> :qv) as similarity
from chunks c join documents d on d.id = c.document_id
where c.id = any(:ids)
"""
).bindparams(bindparam("qv", type_=Vector(EMBED_DIM)))


def rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal rank fusion: score(id) = Σ 1 / (k + rank) over the rankings that contain it.

    Returns (id, score) sorted by score descending; ties go to the better single rank, then lower id.
    """
    scores: dict[int, float] = {}
    best_rank: dict[int, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            best_rank[item] = min(best_rank.get(item, rank), rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], best_rank[kv[0]], kv[0]))


def search(
    query: PolicyQuery,
    *,
    session: Session,
    embedder: Embedder,
    min_similarity: float | None = None,
) -> PolicySearchResult:
    """Top `query.top_k` policy chunks for `query`, with a confidence flag (see module docstring)."""
    if min_similarity is None:
        from supportagent.common.settings import get_settings

        min_similarity = get_settings().data.kb_min_similarity
    params = {
        "as_of": query.as_of or clock.today(),
        "embedder": embedder.name,
        "product": query.product,
        "audience": query.audience,
        "n": CANDIDATES,
    }
    qv = embedder.embed_query(query.query)
    session.execute(text("select set_config('hnsw.ef_search', :v, true)"), {"v": str(EF_SEARCH)})
    vector_hits = session.execute(_VECTOR_SQL, {**params, "qv": qv}).all()
    text_hits = session.scalars(_FTS_SQL, {**params, "q": query.query}).all()

    fused = rrf([[row.id for row in vector_hits], list(text_hits)])[: query.top_k]
    best_similarity = max((float(row.similarity) for row in vector_hits), default=0.0)
    chunks: list[PolicyChunk] = []
    if fused:
        rows = {
            row.id: row for row in session.execute(_DETAILS_SQL, {"qv": qv, "ids": [i for i, _ in fused]})
        }
        for chunk_id, score in fused:
            row = rows[chunk_id]
            chunks.append(
                PolicyChunk(
                    doc_id=row.doc_id,
                    version=row.version,
                    title=row.title,
                    section=row.section_path,
                    product=row.product,
                    text=row.text,
                    rrf_score=round(score, 6),
                    similarity=round(float(row.similarity), 6),
                )
            )
    return PolicySearchResult(
        chunks=chunks,
        confident=bool(chunks) and bool(text_hits) and best_similarity >= min_similarity,
        best_similarity=round(best_similarity, 6),
    )
