# 10 · Observability and redaction — Spec

> **Code:** `OBS` · **Location:** `src/supportagent/observability/`, `infra/observability/` · **Milestones:** M2, M4
> **PRD:** F11, NFR-AUDIT, NFR-SEC · **Depends on:** PLAT (settings, contracts) · **Status:** see [tasks.md](tasks.md)

## Purpose
Makes every call **reconstructable and safe to look at**. This module provides:
- PII redaction, applied before any text is stored, traced or displayed
- a version stamp (`prompt`, `policy`, `model`) for every call
- a redacted transcript store
- per-turn tracing into a self-hosted Langfuse
- a report that reassembles one call from audit log, outcome, transcript, recording and trace

It is a **pure library** (plus one compose profile): any module may import it, and it imports no other
domain module. It reaches the bank only over HTTP, in the report.

## Scope
**In:**
- `redaction` (Presidio + regex)
- `versions` (version bundle)
- `transcripts` (JSONL store)
- `tracing` (OpenTelemetry → Langfuse, with a redacting exporter)
- `report` (call reconstruction)
- the Langfuse compose profile `observability`

**Out:**
- field masking in gateway responses: GW (T-GW-03)
- log-key masking: PLAT (T-PLAT-02)
- per-stage latency measurement: VOICE (T-VOICE-04). OBS only *exports* those numbers.
- audio recording: TEL (T-TEL-06)
- guardrail checkers: EVAL (T-EVAL-03), which places them in `observability/guardrails.py` so the
  console can reuse them

## Requirements
- **OBS-R1** (P0 · F11, NFR-SEC) — The system shall redact PII from free text: person names, phone numbers, emails, street addresses, dates of birth, loan and account numbers (digit or spoken-digit form), national IDs. Each is replaced with a typed placeholder (`<PERSON>`, `<PHONE>`, `<DOB>`, `<LOAN_NUMBER>`, …). Monetary amounts and due dates are kept.
- **OBS-R2** (P0 · NFR-SEC) — Redaction shall be fast and fail closed. A regex pre-pass always runs; Presidio runs after it. If Presidio errors, the regex-only result is returned with `fallback=True`, never the raw text. Per-turn p95 latency ≤ 50 ms on the M4 for ≤ 60-word turns.
- **OBS-R3** (P0 · F11, NFR-AUDIT) — The system shall compute a deterministic `Versions{prompt, policy, model}` bundle from the prompt registry, the KB corpus, contact-rule and disclosure config files, and model settings. Identical inputs give identical strings, and any change to a prompt, policy doc, rule file or model changes the respective string.
- **OBS-R4** (P0 · F11, NFR-AUDIT) — The system shall store each call's transcript as append-only, redacted JSONL at `var/transcripts/{call_id}.jsonl`. Turns are ordered, timestamped, speaker-tagged, and carry state and tool-call references. The store returns a `transcript_uri` and redaction cannot be bypassed through its API.
- **OBS-R5** (P0 · F11) — The system shall trace each call as one trace (id derived from `call_id`). It holds a span per turn with child spans for STT, LLM, TTS and each tool call (name, args hash, decision, latency), with retrieved sources (`doc_id@version`, section, score) and the version bundle as attributes. All string attributes are redacted before export. With no tracing backend configured, tracing is a no-op.
- **OBS-R6** (P1 · F11) — The repository shall run a self-hosted Langfuse stack under the compose profile `observability`. It is pinned to a released version, reachable on `:3000` and kept off the default `make up`; the agent exports OTLP traces to it.
- **OBS-R7** (P0 · NFR-AUDIT) — `make call-report CALL=<call_id>` shall reconstruct one call as markdown: outcome and versions, ordered tool calls from the audit log, redacted transcript, KB sources, latency p50/p90, recording path and trace link. It lists every missing artefact, and `--strict` exits non-zero when anything is missing.

## Acceptance criteria
- **OBS-R1:** "Hi, I'm Frank Miller, born March 14 1985, loan 4401 2287 3310, call me on 555-010-0302"
  becomes "Hi, I'm `<PERSON>`, born `<DOB>`, loan `<LOAN_NUMBER>`, call me on `<PHONE>`". "Your payment of
  $412.50 due on November 12 is late" is unchanged. "my last four are four five two one" redacts the
  digit words.
- **OBS-R1:** With `known=["Frank Miller", "1985-03-14"]`, exact and case-insensitive occurrences are
  always redacted, even if the NER model misses them.
- **OBS-R2:** When the Presidio analyzer is monkeypatched to raise, the result has `fallback=True` and no
  raw phone or number remains. A benchmark of 200 sample turns has p95 ≤ 50 ms locally (CI asserts
  ≤ 200 ms to avoid flakiness).
- **OBS-R3:** Editing one prompt file changes only `versions.prompt`. Bumping a policy doc's `version`
  front-matter changes only `versions.policy`. Switching `LLM_PROVIDER` changes only `versions.model`.
- **OBS-R4:** Appending a raw turn containing a phone number stores `<PHONE>`. `read(call_id)` returns
  turns in `seq` order. A second writer for the same `call_id` gets `seq` continuity. The file is
  newline-delimited JSON valid against `TranscriptTurn`.
- **OBS-R5:** With an in-memory span exporter, a scripted 3-turn call produces 1 trace, 3 turn spans and
  the expected tool spans. No exported attribute contains a seeded persona's phone, DOB or loan number.
- **OBS-R6:** `docker compose --profile observability up -d --wait` gives a healthy Langfuse UI at
  `http://localhost:3000`. An agent run with `LANGFUSE_*` set shows the trace with its turn and tool spans.
- **OBS-R7:** For a completed demo call the report has all sections. Deleting its transcript makes
  `--strict` exit 1 with "missing: transcript".

## Interfaces
**Provides (Python):**
- `observability.redaction`:
  - `redact(text, *, known: Iterable[str] = ()) -> Redacted` (fields `text`, `entities: list[str]`, `fallback: bool`)
  - `redact_mapping(obj) -> obj` (recursive over str values)
  - `warmup()`
- `observability.versions`: `compute_versions(settings, *, prompts_dir, policies_dir, config_dir) -> contracts.outcome.Versions`
- `observability.transcripts`: `TranscriptStore(base_dir)` with `.append(call_id, RawTurn)`, `.read(call_id) -> list[TranscriptTurn]`
  and `.uri(call_id)`. `RawTurn` and `TranscriptTurn` live in `contracts/transcript.py` (owned here).
- `observability.tracing`: `setup_tracing(settings) -> TracerProvider | None`, `call_trace(call_id)` (context manager),
  `record_tool_span(...)`, `record_sources(...)`, `RedactingSpanExporter`.
- `observability.report`: `build_report(call_id, *, bank_url, base_dirs) -> CallReport` and the CLI
  `python -m supportagent.observability.report <call_id> [--strict]`.

**Consumes:**
- PLAT `Settings` and `contracts.outcome.Versions`
- the bank ops endpoints `GET /interactions/{call_id}` and `GET /audit?call_id=` (T-BANK-07)
- recordings at `var/recordings/{call_id}.wav` (T-TEL-06)
- prompt files from AGENT's registry (`src/supportagent/agent/prompts/`)
- policy docs at `data/policies/` and rule configs at `data/config/`

## Non-functional
- Redaction p95 ≤ 50 ms per turn. Presidio and spaCy load once per process (~1 s, in `warmup()` at worker start).
- No raw PII is written to disk or exported by this module. Raw text exists only in process memory.
- Langfuse adds ~2.5 GB RAM, so it is opt-in (architecture §6 memory budget).

## Open questions
- None blocking. Langfuse vs Phoenix: Langfuse is chosen (architecture §6), and the OTLP export keeps
  Phoenix as a drop-in alternative.
