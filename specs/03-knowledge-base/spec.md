# 03 · Policy knowledge base — Spec

> **Code:** `KB` · **Location:** `src/supportagent/kb/`, `data/policies/` · **Milestones:** M1
> **PRD:** F6, U4, AC-GROUND, NFR-AUDIT, NFR-ACC · **Depends on:** PLAT (settings, DB plumbing)
> **Status:** see [tasks.md](tasks.md)

## Purpose
The bank's policies are the agent's only source for "what happens if…" answers. This module owns the
synthetic policy corpus, ingests it into Postgres/pgvector as versioned chunks, and provides hybrid
retrieval that returns cited chunks plus a confidence flag. The gateway's `search_policy` tool (T-GW-08)
runs this library in-process. The agent never imports it.

## Scope
**In:** ~20 Markdown policy documents with front-matter; the `kb` database schema; the heading-based
chunker; the embedder interface (Ollama + deterministic fake); the versioned ingestion CLI; hybrid
retrieval (vector + full-text, RRF, filters, threshold); the golden-question set.
**Out:** answering or phrasing (AGENT); MCP exposure, auditing and rate limits (GW); executable contact
rules and disclosure texts (COMP owns `data/config/*.yaml`; the KB docs *describe* the same values and
COMP tests that they agree).

## Requirements
- **KB-R1** (P0 · F6, U4) — The corpus shall contain ~20 synthetic policy documents in `data/policies/` (list in [plan.md](plan.md#corpus)), each with front-matter `doc_id`, `title`, `product` (product code or `all`), `version`, `effective_date`, `audience` (`customer` | `internal`), and with product-specific figures that match the bank's loan products.
- **KB-R2** (P0 · F6, NFR-AUDIT) — The `kb` database shall store documents and chunks with a 768-d `vector` (HNSW, cosine), a `tsvector` (GIN), section path and version, and shall retain every ingested version for audit.
- **KB-R3** (P0 · F6) — The chunker shall split by Markdown heading into chunks of ~300–500 tokens (hard max 600), carrying the section path (`"Late fees > Personal loan"`). It shall never split inside a table or list item.
- **KB-R4** (P0 · F6, NFR-PORT) — Embeddings shall go through an `Embedder` protocol: `OllamaEmbedder` (`nomic-embed-text`, 768-d, `search_document:`/`search_query:` prefixes) by default, and `HashEmbedder` (deterministic, offline, 768-d) for tests and CI.
- **KB-R5** (P0 · F6, NFR-AUDIT) — Ingestion shall be idempotent (same `doc_id`+`version`+content hash → no-op). A new version of a document is added alongside the old one, never overwriting it. A changed content hash under an existing version is an error.
- **KB-R6** (P0 · F6, U4, NFR-ACC) — Retrieval shall merge vector and full-text candidates with reciprocal rank fusion, filter by `product ∈ {requested, all}`, `audience`, and the version effective at `as_of`, and return the top 4 `PolicyChunk`s with `doc_id`, `version`, `section`, `text` and scores.
- **KB-R7** (P0 · F6, AC-GROUND) — Retrieval shall return `confident=false` when the best vector similarity is below `KB_MIN_SIMILARITY`, so the agent offers a follow-up instead of guessing.
- **KB-R8** (P0 · AC-GROUND, U4) — A golden set of ≥ 30 questions with expected `doc_id`s shall measure retrieval. Hit@4 ≥ 0.90 with Ollama embeddings (eval), and the listed must-pass questions pass with the fake embedder in CI.

## Acceptance criteria
- **KB-R1:** Every file in `data/policies/` parses with valid front-matter, its `doc_id` is unique, and its
  `product` is one of `PERSONAL, AUTO, MORTGAGE, SME_WC, SALARY_ADV, all`. The grace days and late fee
  stated in each product doc equal the bank seed's `loan_products` values (cross-check test).
- **KB-R2:** After `make migrate`, `kb.documents` and `kb.chunks` exist with `chunks_embedding_hnsw`
  (`vector_cosine_ops`) and `chunks_tsv_gin` indexes.
- **KB-R3:** Given a doc whose section has a 12-row table, then the table appears whole in exactly one
  chunk. No chunk exceeds 600 tokens (estimated as `ceil(len(text)/4)`, per plan.md).
- **KB-R4:** `HashEmbedder().embed(["x"])` is identical across processes and returns unit-norm 768-d vectors.
- **KB-R5:** Running ingestion twice changes nothing (row counts and ids are stable). Ingesting
  `late-fees` v1.1 keeps v1.0 rows, and retrieval at `as_of` before v1.1's `effective_date` returns v1.0.
- **KB-R6:** `search("Can I pay my car loan off early?", product="AUTO")` returns ≤ 4 chunks, all with
  `product ∈ {AUTO, all}`, and the top result's `doc_id` is `early-repayment-payoff` or `product-auto-loan`.
- **KB-R7:** `search("what's the weather in Paris")` → `confident=false`.
- **KB-R8:** `pytest -m eval tests/kb/test_golden.py` reports hit@4 ≥ 0.90. The CI subset passes.

## Interfaces
**Provides** (Python library, imported by the gateway only):
- `supportagent.kb.retrieval.search(query: PolicyQuery, *, session, embedder) -> PolicySearchResult`
- `supportagent.kb.embed.Embedder` (protocol), `OllamaEmbedder`, `HashEmbedder`, `get_embedder(settings)`
- CLI `python -m supportagent.kb.ingest [PATH=data/policies] [--embedder ollama|fake] [--dry-run]`
- Contracts in `supportagent/contracts/kb.py`: `PolicyQuery{query, product: str|None, audience="customer",
  as_of: date|None, top_k=4}`, `PolicyChunk{doc_id, version, title, section, product, text, rrf_score,
  similarity: float|None}`, `PolicySearchResult{chunks: list[PolicyChunk], confident: bool, best_similarity: float}`.

**Consumes:** `supportagent.common.settings` (`KB_DB`, `OLLAMA_URL`, `EMBED_MODEL`, `KB_MIN_SIMILARITY`),
`supportagent.common.db` (kb session factory).

## Non-functional
- Retrieval p95 < 150 ms (excluding query embedding) on the corpus. Query embedding via local Ollama is ~30–60 ms.
- No network in unit tests. Integration tests use `HashEmbedder`. Only `eval` tests call Ollama.
- The corpus is synthetic, US-rule placeholders (PRD §12).

## Open questions
- `KB_MIN_SIMILARITY` default 0.55 is a starting point. T-KB-06 tunes it on the golden set and records
  the chosen value in plan.md.
