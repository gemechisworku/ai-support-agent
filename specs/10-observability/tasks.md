# 10 · Observability and redaction — Tasks

> Each task is one GitHub issue and one PR. The field bullets are parsed by `scripts/tasks.py`, so keep
> their exact format. Status is `todo` or `done`. Issue is `—` until `make issues-sync` fills it in.
> Ids are never reused. Order tasks by dependency.

## Tasks

### T-OBS-01 · PII redaction with Presidio
- **Status:** done
- **Milestone:** M2
- **Issue:** #38
- **Depends on:** T-PLAT-02
- **Requirements:** OBS-R1, OBS-R2

Build `supportagent.observability.redaction` as described in [plan.md](plan.md#approach). It has three
parts:
- a compiled regex pre-pass (phones, emails, loan and account numbers, digit runs ≥ 4, spoken-digit runs
  ≥ 4 words, DOB with context, `known` values)
- Presidio `AnalyzerEngine` with spaCy `en_core_web_sm` and the custom recognizers in `recognizers.py`
- `AnonymizerEngine` emitting `<TYPE>` placeholders

Generic `DATE_TIME`/`NRP` stay disabled, and an allow-list keeps the bank name, agent name and product
names. Engines are lazy singletons, and `warmup()` loads them. On any Presidio exception, return the
regex-only result with `fallback=True` and log a warning (without the text).

**Acceptance criteria**
- [x] `redact()` replaces every entity in `fixtures/pii_samples.yaml` with the right placeholder.
- [x] Amounts (`$412.50`, "four hundred twelve dollars") and due dates stay unchanged.
- [x] `known=[...]` values are always redacted, case-insensitively, including possessives ("Frank's").
- [x] `redact_mapping()` redacts nested dict and list string values and leaves non-strings untouched.
- [x] A Presidio failure produces `fallback=True` with no raw structured PII.
- [x] The en_core_web_sm wheel is pinned in `pyproject.toml`, and `uv sync` installs it in CI and Docker.
  *(Measured p95 2.2 ms over the 200-turn benchmark on the M4.)*

**Tests**
- `tests/observability/test_redaction.py` (unit): the parametrised sample table, with positive and
  must-keep cases; `known` values; spoken digits; `redact_mapping`; the fail-closed path via monkeypatched
  analyzer.
- `tests/observability/test_redaction_perf.py` (unit): 200 synthetic turns after `warmup()`; asserts
  p95 ≤ 200 ms in CI and prints the p95 (target ≤ 50 ms on M4).

**Files:** `src/supportagent/observability/redaction.py`, `src/supportagent/observability/recognizers.py`, `tests/observability/test_redaction.py`, `tests/observability/test_redaction_perf.py`, `tests/observability/fixtures/pii_samples.yaml`, `pyproject.toml`

### T-OBS-02 · Version stamping for prompts, policies and models
- **Status:** done
- **Milestone:** M2
- **Issue:** #39
- **Depends on:** T-PLAT-04
- **Requirements:** OBS-R3

Implement `compute_versions()` using the formula in [plan.md](plan.md#data-model--contracts). It
returns `contracts.outcome.Versions` and is pure: it is given directories and settings and reads only
those. Missing inputs give explicit `"none"` components, never an exception, so early milestones work
before the prompt registry or corpus exist. The agent calls it once per call at session start (T-AGENT-02).

**Acceptance criteria**
- [x] Identical inputs give identical `Versions` across processes (no reliance on mtime or dict order).
- [x] Changing one prompt file changes only `prompt`.
- [x] Bumping one policy doc's front-matter `version` changes only `policy`.
- [x] Changing `LLM_PROVIDER` or `LLM_MODEL` changes only `model`.
- [x] Missing `prompts/VERSION` or `data/config/*.yaml` yields a `none` component.

**Tests**
- `tests/observability/test_versions.py` (unit): tmp-dir fixtures for each change type; determinism; missing inputs.

**Files:** `src/supportagent/observability/versions.py`, `tests/observability/test_versions.py`

### T-OBS-03 · Redacted transcript store
- **Status:** todo
- **Milestone:** M2
- **Issue:** #40
- **Depends on:** T-OBS-01
- **Requirements:** OBS-R4

Add `contracts/transcript.py` (`RawTurn`, `TranscriptTurn`, `ToolRef`, `RedactionInfo`) and
`TranscriptStore`. `append()` redacts `text` (with the turn's `known_pii`) and `tool.args` via
`redact_mapping`. It then assigns `seq` (the next line number, under `fcntl.flock`) and writes one JSON
line to `{base_dir}/{call_id}.jsonl`. `known_pii` is never persisted. `uri()` returns
`file://var/transcripts/{call_id}.jsonl`, which is relative to the repo. The base dir comes from
settings (`TRANSCRIPTS_DIR`, default `var/transcripts`).

**Acceptance criteria**
- [ ] A raw turn with a phone number and the persona's name is stored with placeholders only.
- [ ] `read()` returns `TranscriptTurn`s ordered by `seq`. Two interleaved writers produce gapless, unique `seq`s.
- [ ] An invalid `call_id` (path separators, `..`) is rejected.
- [ ] No public method writes un-redacted text.

**Tests**
- `tests/observability/test_transcripts.py` (unit): redaction applied, `known_pii` not persisted, ordering,
  concurrent append via threads, path traversal rejected, JSONL schema round-trip.

**Files:** `src/supportagent/contracts/transcript.py`, `src/supportagent/observability/transcripts.py`, `tests/observability/test_transcripts.py`, `.env.example`

### T-OBS-04 · Langfuse stack and agent tracing
- **Status:** todo
- **Milestone:** M4
- **Issue:** #77
- **Depends on:** T-OBS-01, T-AGENT-08
- **Requirements:** OBS-R5, OBS-R6

Two parts.
1. Vendor the pinned Langfuse v3 compose file into `infra/observability/langfuse.compose.yml`.
   - Every service gets `profiles: [observability]`, and only `3000` is published.
   - Add `LANGFUSE_INIT_*` env so a local project and keys matching `.env.example` exist on first boot.
   - `include:` it from the root `docker-compose.yml`, and add `make obs-up` / `make obs-down`.
2. Implement `tracing.py`:
   - `setup_tracing()` builds a `TracerProvider` with `RedactingSpanExporter(OTLPSpanExporter(...))`
     when the `LANGFUSE_*` keys are set, and returns `None` otherwise (no-op).
   - `call_trace(call_id)` uses the deterministic trace id `uuid5(NAMESPACE, call_id)`.
   - Helpers record tool spans (name, `args_hash`, decision, latency) and KB sources.
   - Turn spans carry the VOICE per-stage latency attributes.
   - The agent worker calls these helpers (wiring lives in AGENT; this task adds the calls at the
     existing hooks from T-AGENT-08).

**Acceptance criteria**
- [ ] `docker compose --profile observability up -d --wait` → Langfuse healthy at `:3000`. `make up` alone does not start it.
- [ ] With in-memory export, a scripted 3-turn call yields 1 trace, 3 turn spans and its tool spans, with `doc_id@version` source attributes.
- [ ] No exported span or event attribute contains seeded persona PII.
- [ ] With no Langfuse keys, the agent runs with zero exporter errors.
- [ ] A live run shows the call trace in the Langfuse UI.

**Tests**
- `tests/observability/test_tracing.py` (unit): `InMemorySpanExporter` behind `RedactingSpanExporter`;
  span tree shape; redaction of attributes and events; no-op when unconfigured; deterministic trace id.
- `tests/observability/test_langfuse_stack.py` (live): send one span via OTLP, fetch it via
  `GET /api/public/traces/{id}`.

**Files:** `infra/observability/langfuse.compose.yml`, `docker-compose.yml`, `Makefile`, `src/supportagent/observability/tracing.py`, `src/supportagent/agent/worker.py`, `tests/observability/test_tracing.py`, `tests/observability/test_langfuse_stack.py`, `.env.example`

### T-OBS-05 · Call reconstruction report
- **Status:** todo
- **Milestone:** M4
- **Issue:** #78
- **Depends on:** T-OBS-04, T-BANK-07, T-TEL-06
- **Requirements:** OBS-R7

`python -m supportagent.observability.report <call_id> [--strict] [--out var/reports/]` assembles one
call (NFR-AUDIT):
- the interaction outcome and versions, from `GET /interactions/{call_id}`
- tool calls in time order, from `GET /audit?call_id=`
- the redacted transcript, from `TranscriptStore`
- KB sources, latency p50/p90 and the recording path, which must exist (`var/recordings/{call_id}.wav`)
- the Langfuse trace URL

It renders markdown and prints a "Missing" section. `--strict` exits 1 if any artefact is missing.
Add `make call-report CALL=<id>`.

**Acceptance criteria**
- [ ] For a complete fixture call, all sections render and the exit code is 0 with `--strict`.
- [ ] Removing the transcript, recording or versions makes each one appear under "Missing", and `--strict` exits 1.
- [ ] The report contains no raw PII: the transcript is already redacted, and outcome summary and free-text fields are passed through `redact()`.
- [ ] The make target works against the running compose stack.

**Tests**
- `tests/observability/test_report.py` (unit): `httpx.MockTransport` for the bank ops API, tmp transcript
  and recording files; complete vs missing artefacts; strict exit codes; PII scan of the output.

**Files:** `src/supportagent/observability/report.py`, `Makefile`, `tests/observability/test_report.py`
