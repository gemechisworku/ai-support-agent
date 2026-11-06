# 03 · Policy knowledge base — Tasks

> Each task is one GitHub issue and one PR. The field bullets are parsed by `scripts/tasks.py`, so keep
> their exact format. Status is `todo` or `done`. Issue is `—` until `make issues-sync` fills it in.
> Ids are never reused. Order tasks by dependency.

## Tasks

### T-KB-01 · Synthetic policy corpus
- **Status:** todo
- **Milestone:** M1
- **Issue:** #21
- **Depends on:** T-PLAT-01
- **Requirements:** KB-R1

Write the 20 policy documents in [plan.md](plan.md#corpus) under `data/policies/`, each with valid
front-matter and 2–6 headed sections of plain, customer-facing text. Add `frontmatter.py`
(`PolicyDocMeta` validation). Product figures (grace days, late-fee rule, promise window 14 days,
reporting at 30 DPD, etc.) must be stated exactly. The bank seed (T-BANK-02) and the COMP config
(T-COMP-01) use the same values.

**Acceptance criteria**
- [ ] 20 docs, unique `doc_id`s, valid `product`/`audience`/`version`/`effective_date`
- [ ] Each product doc states grace days and late fee matching the plan table
- [ ] Delinquency stages, promise-to-pay rules, disputes and hardship docs state the values in architecture §5
- [ ] Docs contain no real bank names, people or phone numbers

**Tests**
- `tests/kb/test_corpus.py` (unit): every doc parses; required keys and allowed values; unique ids; each product code has exactly one product doc
- `tests/kb/test_corpus_bank_consistency.py` (unit): grace days and late fee per product equal the bank seed's product definitions (skipped with a clear reason until T-BANK-02 lands)

**Files:** `data/policies/*.md`, `src/supportagent/kb/frontmatter.py`, tests above

### T-KB-02 · KB schema and migration (documents, chunks, HNSW, tsvector)
- **Status:** todo
- **Milestone:** M1
- **Issue:** #22
- **Depends on:** T-PLAT-03
- **Requirements:** KB-R2

SQLAlchemy models and the first Alembic migration for database `kb`: `documents` and `chunks` with a
`vector(768)` column, a generated `tsvector`, an HNSW cosine index and a GIN index. Add the contracts in
`supportagent/contracts/kb.py` (`PolicyQuery`, `PolicyChunk`, `PolicySearchResult`).

**Acceptance criteria**
- [ ] Migration enables `vector` (idempotent) and creates both tables and indexes from the plan
- [ ] `unique(doc_id, version)` enforced
- [ ] Contracts validate `top_k` in 1..8 and `product` in the allowed codes or `None`

**Tests**
- `tests/kb/test_schema.py` (integration): upgrade/downgrade; indexes exist with the right access methods; the generated tsv is populated on insert
- `tests/contracts/test_kb_contracts.py` (unit): validation rules

**Files:** `src/supportagent/kb/models.py`, `migrations/kb/versions/0001_initial.py`, `src/supportagent/contracts/kb.py`, tests above

### T-KB-03 · Heading-based markdown chunker
- **Status:** todo
- **Milestone:** M1
- **Issue:** #23
- **Depends on:** T-PLAT-01
- **Requirements:** KB-R3

Pure function `chunk(markdown: str, meta: PolicyDocMeta) -> list[ChunkDraft]`. It splits on headings,
merges short sibling sections up to ~500 tokens, splits long sections on paragraph boundaries (target
300–500, hard max 600), keeps tables and list items intact, and records `section_path` as the heading
trail joined with `" > "`.

**Acceptance criteria**
- [ ] Section paths reflect nesting (`"Late fees > Auto loan"`)
- [ ] Tables and list items never split; no chunk > 600 tokens
- [ ] Output is deterministic and preserves source order (`ord`)

**Tests**
- `tests/kb/test_chunker.py` (unit): nesting, merge of short sections, split of a long section, table integrity, token bounds over the whole corpus

**Files:** `src/supportagent/kb/chunker.py`, `tests/kb/test_chunker.py`

### T-KB-04 · Embedder interface: Ollama and deterministic fake
- **Status:** done
- **Milestone:** M1
- **Issue:** #24
- **Depends on:** T-PLAT-02
- **Requirements:** KB-R4

The `Embedder` protocol (`dim`, `embed_documents(texts)`, `embed_query(text)`). `OllamaEmbedder` calls
`POST {OLLAMA_URL}/api/embed` with model `EMBED_MODEL`, batches of 32 and the nomic task prefixes.
`HashEmbedder` is offline and deterministic. `get_embedder(settings)` selects by `KB_EMBEDDER=ollama|fake`.

**Acceptance criteria**
- [x] Both return unit-norm 768-d `list[float]`; `OllamaEmbedder` raises a clear error on dimension mismatch or connection failure
- [x] `HashEmbedder` gives identical vectors across runs and similar vectors for texts sharing words
- [x] Prefixes `search_document: ` / `search_query: ` applied by the Ollama embedder only

**Tests**
- `tests/kb/test_embed.py` (unit): HashEmbedder determinism/norm/similarity ordering; OllamaEmbedder against `httpx.MockTransport` (request shape, batching, prefixing, error mapping)
- `tests/kb/test_embed_ollama.py` (eval): real Ollama returns 768-d vectors

**Files:** `src/supportagent/kb/embed.py`, tests above

### T-KB-05 · Versioned ingestion CLI
- **Status:** todo
- **Milestone:** M1
- **Issue:** #25
- **Depends on:** T-KB-01, T-KB-02, T-KB-03, T-KB-04
- **Requirements:** KB-R5, KB-R2

`ingest(path, session, embedder)` plus `python -m supportagent.kb.ingest [PATH] [--embedder ollama|fake]
[--dry-run]`. Per document it parses, hashes, then either skips (same doc_id+version+hash), inserts (new
version) or fails (same version, different hash). Each document goes in one transaction. Prints a summary
(`added / skipped / chunks`). Add `make kb-ingest`.

**Acceptance criteria**
- [ ] Second run is a no-op (exit 0, "0 added")
- [ ] New version is added and the old one kept; same version with edited content → non-zero exit naming the doc
- [ ] `--dry-run` writes nothing and lists the planned actions

**Tests**
- `tests/kb/test_ingest.py` (integration, HashEmbedder): full corpus ingest counts; idempotency; version add; hash-conflict error; dry-run

**Files:** `src/supportagent/kb/ingest.py`, `Makefile` (`kb-ingest`), tests above

### T-KB-06 · Hybrid retrieval (RRF, filters, threshold) with golden questions
- **Status:** todo
- **Milestone:** M1
- **Issue:** #26
- **Depends on:** T-KB-05
- **Requirements:** KB-R6, KB-R7, KB-R8

`search(PolicyQuery, session, embedder) -> PolicySearchResult`. It takes 20 vector candidates (cosine,
`hnsw.ef_search=40`) and 20 full-text candidates (`websearch_to_tsquery('english', q)`, `ts_rank_cd`),
restricted to the effective version per `doc_id` at `as_of`, `product ∈ {query.product, 'all'}` and
`audience`. It fuses them with RRF k=60, returns the top `top_k` (4), and sets `confident` from
`best_similarity ≥ KB_MIN_SIMILARITY`. Write `tests/kb/golden_questions.yaml` (≥ 30 items:
`question`, `product`, `expected_doc_ids`, `must_pass_ci: bool`) and tune the threshold. Record the
value in plan.md.

**Acceptance criteria**
- [ ] Spec acceptance criteria for KB-R6 and KB-R7 hold
- [ ] Version filter: a v1.1 effective in the future is ignored; the v1.0 answer is returned
- [ ] Golden eval: hit@4 ≥ 0.90 with Ollama; all `must_pass_ci` questions pass with HashEmbedder
- [ ] `KB_MIN_SIMILARITY` added to settings and `.env.example` with the tuned default

**Tests**
- `tests/kb/test_rrf.py` (unit): fusion maths, ties, one-sided candidates
- `tests/kb/test_retrieval.py` (integration): filters, top-k, version selection, off-topic → not confident
- `tests/kb/test_golden.py` (integration for the CI subset; `eval` for the full set with Ollama): hit@4 and MRR printed

**Files:** `src/supportagent/kb/retrieval.py`, `tests/kb/golden_questions.yaml`, tests above
