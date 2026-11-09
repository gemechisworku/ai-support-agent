# 10 · Observability and redaction — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md).

## Approach
- **Redaction:** a two-stage pipeline.
  1. A compiled-regex pre-pass catches structured PII cheaply and deterministically: phones, emails,
     loan and account numbers, long digit runs, spoken-digit runs, DOB-with-context, and caller-supplied
     `known` values. It runs first and always.
  2. Presidio's `AnalyzerEngine` runs on the pre-passed text with the spaCy `en_core_web_sm` model
     (small, ~12 MB, ~5 ms/turn on M4) for `PERSON` and `LOCATION`/address, plus custom pattern
     recognizers registered into the same registry.
  - `AnonymizerEngine` replaces spans with `<TYPE>` placeholders.
  - Presidio's generic `DATE_TIME` and `NRP` are deliberately **disabled**, so due dates and amounts
    survive for accuracy audits (NFR-ACC).
  - DOBs are caught only by the context-aware recognizer ("born", "birthday", "date of birth", "DOB")
    or by `known` values.
  - Pre-pass order (`recognizers.ORDERED`): `known` values (longest first; a name also matches each of
    its words, an ISO date also matches "March 14, 1985", "14 March 1985", "03/14/1985"; a trailing
    "'s" is kept), email, national id (`123-45-6789`), DOB-with-context, street address, loan and
    account numbers after the words "loan" / "account" (the keyword stays readable), phone, spoken
    digits (4+ digit words), then any other run of 4+ digits as `<NUMBER>` unless it sits inside money
    (`$1,250.00`, "500 dollars") or a calendar date. A bare year with no month ("since 2019") is
    therefore redacted; that over-redaction is accepted.
  - The custom recognizers are `EntityRecognizer`s that report only the PII group of the same compiled
    patterns, so Presidio never swallows the keyword. NER results that overlap an existing placeholder
    or match the allow-list (`BANK_NAME`, `AGENT_NAME`, product names) are dropped. Presidio's
    `LOCATION` becomes `<LOCATION>`.
  - Redaction reads only the persona settings group, so it works in processes without database settings.
- **Versions:** pure functions hashing file contents in a stable (sorted-path) order. See the formula below.
- **Transcripts:** `TranscriptStore.append()` takes a `RawTurn`, calls `redact()` internally and appends
  one JSON line under a per-file lock (`fcntl.flock`). No public method accepts pre-redacted text, so
  redaction cannot be skipped.
- **Tracing:** OpenTelemetry SDK.
  - LiveKit Agents emits OTel spans when given a tracer provider
    (`livekit.agents.telemetry.set_tracer_provider`).
  - We add our own spans for state, tool calls and sources.
  - `RedactingSpanExporter` wraps `OTLPSpanExporter` and rewrites each `ReadableSpan`'s string
    attributes and event attributes through `redact_mapping` before export.
  - Langfuse ingests OTLP at `{LANGFUSE_HOST}/api/public/otel`, using Basic auth built from the public
    and secret keys.
  - The trace id is `uuid5(NAMESPACE, call_id)` so the report can link to it.
- **Langfuse stack:** the upstream `docker-compose.yml` from the Langfuse v3 release, vendored into
  `infra/observability/langfuse.compose.yml` and pinned to an exact image tag.
  - It runs langfuse-web, langfuse-worker, postgres, clickhouse, redis and minio, with only `:3000`
    published.
  - It is included into the root compose with `include:`, and every service carries
    `profiles: [observability]`.
  - Init env (`LANGFUSE_INIT_*`) creates a local org, project and API keys that match `.env.example`.
- **Report:** gathers artefacts from the bank ops API, the transcript store, the recordings dir and the
  trace link, renders markdown (Jinja-free f-strings), and lists missing pieces.

## File layout
```
src/supportagent/observability/
  __init__.py
  redaction.py        regex pre-pass, Presidio engines (lazy singletons), redact(), redact_mapping(), warmup()
  recognizers.py      LoanNumberRecognizer, AccountNumberRecognizer, DobContextRecognizer, SpokenDigitsRecognizer
  versions.py         compute_versions()
  transcripts.py      TranscriptStore
  tracing.py          setup_tracing(), call_trace(), record_tool_span(), record_sources(), RedactingSpanExporter
  report.py           build_report(), CLI (__main__ via `python -m supportagent.observability.report`)
  guardrails.py       (written by T-EVAL-03; pure checkers shared by evals and console)
src/supportagent/contracts/transcript.py   RawTurn, TranscriptTurn, ToolRef
infra/observability/langfuse.compose.yml
tests/observability/
  test_redaction.py  test_redaction_perf.py  test_versions.py  test_transcripts.py
  test_tracing.py    test_report.py          test_langfuse_stack.py (live)
  fixtures/pii_samples.yaml   (synthetic sentences + expected redactions)
```

## Data model / contracts
```python
class RawTurn(BaseModel):          # contracts/transcript.py
    speaker: Literal["agent", "caller", "system"]
    text: str
    state: str | None = None        # agent state name, e.g. "verify"
    tool: ToolRef | None = None     # {name, args (dict), decision: "allowed"|"denied", latency_ms}
    ts: datetime                    # tz-aware UTC
    known_pii: list[str] = []       # values the gateway/agent knows are PII for this call (not persisted)

class TranscriptTurn(BaseModel):    # what is stored
    call_id: str; seq: int; ts: datetime; speaker: ...; text: str; state: str | None
    tool: ToolRef | None            # args redacted via redact_mapping
    redaction: RedactionInfo        # {entities: list[str], fallback: bool}
```

**Version formula** (`sha8` = first 8 hex chars of sha256 over sorted `relpath + "\0" + bytes`):
- `prompt` = `f"{registry_version}+{sha8(prompts_dir/**/*.md|*.yaml|*.yml)}"`, where `registry_version` is
  the stripped text of `prompts/VERSION` (owned by AGENT). Hidden paths and `__pycache__` are skipped;
  each file contributes `relpath \0 bytes \0` in sorted POSIX relative-path order.
- `policy` = `f"kb:{sha8(sorted doc_id@version lines from policy front-matter)};rules:{contact_rules.version};disc:{disclosures.version}"`.
  The rules and disclosures versions are read from the `version:` key of
  `data/config/contact_rules.yaml` and `data/config/disclosures.yaml`. The KB part follows front-matter
  `doc_id@version` only (a policy change must bump its version); a doc without front matter counts as
  `<file stem>@unversioned`.
- `model` = `f"llm:{provider}/{model};stt:{whisper_model};tts:{tts_voice};embed:{embed_model}"`, from `Settings.models`.
- Any missing or malformed input yields the component `none` (`none+none`, `kb:none;rules:none;disc:none`);
  `compute_versions()` never raises. Directory arguments default to the repo's `agent/prompts`,
  `data/policies` and `data/config`; `prompt_version`, `policy_version` and `model_version` are public too.

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| NER model | spaCy `en_core_web_sm` 3.8 | Smallest English model. Names are the only NER-dependent entity; everything else is pattern-based. `_lg` costs ~500 MB for marginal gain. |
| Order | regex first, Presidio second | Deterministic coverage of numbers even if NER fails, and the fail-closed fallback reuses it |
| Dates | Generic DATE_TIME off | Due dates must stay visible to audit amounts and dates (NFR-ACC) |
| Trace backend | OTLP → Langfuse | Native LiveKit integration; Phoenix swappable by endpoint only |
| Transcript format | JSONL per call | Append-only, greppable, trivially reconstructable; no DB coupling |
| Langfuse compose | Vendored and pinned upstream file | Multi-container stack; vendoring makes upgrades explicit |

## Dependencies
- Runtime: `presidio-analyzer`, `presidio-anonymizer`, and `en-core-web-sm` as a direct-URL wheel pinned
  in `pyproject.toml` (`https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl`).
  The URL is pinned through `[tool.uv.sources]` and its sha256 is in `uv.lock`; CI and Docker install it
  with `uv sync --frozen`. A non-frozen `uv run` revalidates the URL against GitHub, so during a GitHub
  outage use `UV_FROZEN=1`.
- Tracing: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `httpx` (report).
- Services: Langfuse v3 stack (compose profile `observability`).

## Test strategy
- **unit:**
  - redaction over `fixtures/pii_samples.yaml`: positive cases and must-keep cases (amounts, due dates)
  - fail-closed via monkeypatch
  - a perf benchmark with a generous CI bound
  - versions over tmp dirs
  - the transcript store in `tmp_path`
  - tracing with `InMemorySpanExporter` wrapped by `RedactingSpanExporter`
  - the report with `httpx.MockTransport` and tmp artefacts
- **integration:** none needed. The report's bank calls are faked; the real round trip is covered by T-EVAL-06.
- **live:** `test_langfuse_stack.py` starts the profile, sends one span and asserts it is visible via
  the Langfuse public API (`GET /api/public/traces/{id}`).

## Risks
- Spoken-digit and NER false negatives on noisy STT text. Mitigated by `known` values from the call
  context and by eval checks (T-EVAL-03 scans stored transcripts for persona PII).
- Langfuse upstream compose changes between versions. Mitigated by vendoring a pinned file and upgrading in its own PR.
- Over-redaction of product names as `<PERSON>`. Mitigated by an allow-list (bank name, agent name, product names) passed to the analyzer.
