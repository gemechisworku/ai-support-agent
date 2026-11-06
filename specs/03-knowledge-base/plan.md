# 03 · Policy knowledge base — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md). Design source: [architecture.md §5](../../docs/architecture.md).

## Approach
Policies are plain Markdown with YAML front-matter, so they are reviewable in PRs and versioned in git.
Ingestion parses the front-matter, chunks by heading (`chunker.py`, pure), embeds chunks with
`search_document: ` + section path + text, and upserts into the `kb` database in one transaction per
document. Retrieval runs two SQL queries, cosine kNN over the HNSW index and `websearch_to_tsquery` over
the GIN index (20 candidates each), then fuses them in Python with RRF (`k=60`) and returns the top 4.
Version filtering picks, per `doc_id`, the highest `effective_date ≤ as_of`. That keeps old versions
queryable for audit and replay.

## Corpus
`data/policies/<doc_id>.md`. Every doc has front-matter `doc_id, title, product, version, effective_date,
audience` and is written in plain customer-facing language. Figures (fees, days) must match the bank's
`loan_products` and the COMP config. Product docs (product ≠ `all`) also carry machine-readable
`grace_days` and `late_fee: {type: flat|pct, value, cap?}` (the bank's `late_fee_rule` shape), so the
bank cross-check compares values exactly; other docs must not set them. `doc_id` equals the file name.
All v1.0 docs are effective 2026-01-01.

Product figures chosen in T-KB-01 (T-BANK-02 seeds the same):

| Product | rate_type | APR | Term (months) | Prepayment fee |
|---|---|---|---|---|
| PERSONAL | fixed | 8.99–24.99% | 12–60 | none |
| AUTO | fixed | 5.49–14.99% | 24–72 | none |
| MORTGAGE | variable | 5.25–7.75% | 120–360 | 2% of principal repaid early in first 36 months |
| SME_WC | variable | 9.50–19.50% | 6–36 | 1% of principal repaid early in first 12 months |
| SALARY_ADV | fixed | 18.00–35.99% | 1–6 | none |

Other stated values: no penalty interest (normal contract rate keeps accruing); a payment is past due
from the day after its due date, and the late fee is added when still unpaid at the end of the grace
period; returned-payment fee $15; payoff quote free and valid 10 days; mobile payments post same business
day before 8 p.m. local, branch immediately, transfers pending up to 2 business days; disputes answered
within 30 days; complaints acknowledged in 2 and resolved in 15 business days; hardship deferral up to 3
months (a specialist decides). `required-disclosures` holds each text as `## <key> (version 1.0)`
followed by a `> text` line, identical to `data/config/disclosures.yaml`.

| doc_id | product | audience | Content |
|---|---|---|---|
| product-personal-loan | PERSONAL | customer | terms, APR range, 15-day grace, $25 flat late fee |
| product-auto-loan | AUTO | customer | terms, 10-day grace, 5% late fee capped at $50 |
| product-mortgage | MORTGAGE | customer | terms, 15-day grace, 4% late fee |
| product-sme-working-capital | SME_WC | customer | terms, 7-day grace, $75 flat late fee |
| product-salary-advance | SALARY_ADV | customer | terms, 3-day grace, $10 flat late fee |
| repayment-schedules | all | customer | due-date rules, weekends/holidays, changing due date (human only) |
| late-fees-penalty-interest | all | customer | how fees and penalty interest accrue, per-product table |
| delinquency-stages | all | customer | reminders → fee → bureau at 30 DPD → collections at 60 → default at 90 |
| credit-bureau-reporting | all | customer | what is reported, when, how to correct |
| hardship-assistance | all | customer | eligibility, restructuring options; humans decide |
| promise-to-pay-rules | all | customer | max 14 days, partial payments, broken promise follow-up |
| payment-channels | all | customer | mobile, branch, transfer; posting times (pending up to 2 business days) |
| early-repayment-payoff | all | customer | prepayment, payoff quote, per-product penalties |
| disputes-error-resolution | all | customer | how to dispute; collection paused while open |
| complaints-escalation | all | customer | complaint routes, timelines, human contact |
| identity-verification-privacy | all | internal | right-party check, DOB + last 4, lockout after 2 failures |
| contact-calling-rules | all | internal | window 08:00–21:00 local, 7-in-7, 7-day cool-off, DNC, consent |
| required-disclosures | all | internal | AI/recording and collections disclosure texts with versions |
| faq | all | customer | top inbound questions (what do I owe, when is it due, how to pay) |
| fees-schedule | all | customer | all fees in one table |

## File layout
```
data/policies/*.md                 the 20 docs above
src/supportagent/kb/
  frontmatter.py   parse + validate front-matter → PolicyDocMeta
  chunker.py       pure: chunk(markdown, meta) -> list[ChunkDraft]
  embed.py         Embedder protocol, OllamaEmbedder (httpx), HashEmbedder, get_embedder()
  models.py        SQLAlchemy: documents, chunks
  ingest.py        ingest(path, session, embedder) + __main__ CLI
  retrieval.py     search(query, session, embedder) -> PolicySearchResult; rrf()
src/supportagent/contracts/kb.py   PolicyQuery, PolicyChunk, PolicySearchResult
migrations/kb/versions/0001_initial.py
tests/kb/ test_corpus.py test_chunker.py test_embed.py test_schema.py test_ingest.py
          test_rrf.py test_retrieval.py test_golden.py golden_questions.yaml
```

## Data model / contracts
| Table | Columns |
|---|---|
| documents | id bigserial pk · doc_id text · version text · title · product text · audience text · effective_date date · content_hash text(sha256) · source_path · ingested_at · unique(doc_id, version) |
| chunks | id bigserial pk · document_id fk · ord int · section_path text · text text · token_count int · embedding vector(768) · tsv tsvector generated always as (to_tsvector('english', section_path ‖ ' ' ‖ text)) stored |

Indexes: `chunks_embedding_hnsw` USING hnsw (embedding vector_cosine_ops) (m=16, ef_construction=64);
`chunks_tsv_gin` USING gin(tsv); `documents(doc_id, effective_date)`.

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| Token counting | `len(text)/4` heuristic, with tiktoken optional | no heavy deps; sizes are approximate anyway |
| Fusion | RRF k=60, 20 candidates per retriever | standard; robust to score-scale mismatch |
| Confidence | best cosine similarity among returned vector hits ≥ `KB_MIN_SIMILARITY` (default 0.55) | RRF scores are rank-only and not comparable across queries |
| Fake embedder | SHA-256 of lower-cased word unigrams+bigrams hashed into 768 buckets, L2-normalised | deterministic and lexical, so tests are meaningful offline |
| Versions | keep all; filter at query time | NFR-AUDIT replay; architecture §5 |
| Audience | default `customer`; `internal` only on explicit request | agent should not recite internal procedures |

## Dependencies
`python-frontmatter` (or `pyyaml` + a split on `---`), `pgvector` (SQLAlchemy `Vector` type), `httpx`
(Ollama), `sqlalchemy`, `psycopg`. No new services: Postgres comes from PLAT and the image is already
`pgvector/pgvector:pg16`.

## Test strategy
- **unit:** front-matter validation, chunker (sizes, section paths, tables intact), HashEmbedder
  determinism and norm, RRF maths, version selection logic.
- **integration:** schema and indexes; ingestion idempotency and versioning; retrieval filters, top-4,
  confidence flag, and the must-pass golden subset with HashEmbedder against `kb_test`.
- **eval:** full golden set with OllamaEmbedder (needs `ollama pull nomic-embed-text`). Report hit@4
  and MRR. Used to tune `KB_MIN_SIMILARITY`.

## Risks
- Fake embeddings make vector recall look worse than the real thing. The CI subset is chosen to pass on
  lexical overlap, and the real quality bar is the `eval` test.
- Policy figures drifting from the seed: the cross-check test in T-KB-01 fails fast.
