# 01 · Platform and contracts — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md).

## Approach
Keep the foundation thin, so it doesn't fight the modules built on top of it:
- **Settings** use pydantic-settings with nested groups. Each module reads only its own group.
- **Logging** uses the stdlib `logging` module with a JSON formatter that masks PII in `extra=` fields
  (by key) as it serialises, so nothing reaches a handler unmasked. There is no structlog dependency.
  Message text is not scanned: data goes in `extra=`, and free-text redaction is OBS's job.
- **Databases** use synchronous SQLAlchemy 2.0 with psycopg 3. The services are low-concurrency, so
  sync keeps tests simple. FastAPI runs sync endpoints in its threadpool.
- **Migrations:** each database gets its own Alembic directory (`migrations/bank`, `migrations/kb`),
  and `alembic.ini` names both, so modules add revisions without colliding.

## File layout
```
src/supportagent/common/
  settings.py      Settings(postgres, data, services, models, livekit, langfuse, persona), get_settings(),
                   load_settings(env_file), env_var_names()
  logging.py       JsonFormatter (masks `extra=` fields via mask_pii), configure_logging(), get_logger(),
                   bind_call()
  db.py            engines, session_scope(), BankBase, KbBase (DeclarativeBase)
  clock.py         now(), today(), freeze_clock() (contextvar-based)
src/supportagent/contracts/
  outcome.py       CallOutcome + enums (PRD §8)
  money.py         Money = Annotated[Decimal, ...] (2 dp, >= 0)
migrations/
  bank/env.py, versions/      (target_metadata = BankBase.metadata)
  kb/env.py, versions/        (target_metadata = KbBase.metadata)
alembic.ini                   [bank] and [kb] sections
scripts/doctor.sh             make doctor
docs/contracts/call_outcome.schema.json   generated
tests/conftest.py             settings / bank_db / kb_db / frozen_clock fixtures
tests/common/, tests/contracts/
```

## Data model / contracts
`CallOutcome` (PRD §8), with types:

| Field | Type |
|---|---|
| call_id | `str` (UUID4) |
| direction | `Direction` = inbound \| outbound |
| campaign | `Campaign \| None` = pre_due \| missed_payment \| callback \| inbound \| playground |
| customer_id, loan_id | `int \| None` |
| contact_result | `ContactResult` = right_party \| third_party \| no_answer \| voicemail \| wrong_number \| busy |
| verified | `bool` |
| disclosures_given | `list[str]` (disclosure ids with version, e.g. `ai_recording@1`) |
| intent | `list[Intent]` |
| reason_code | `ReasonCode \| None` = forgot \| insufficient_funds \| income_disruption \| dispute \| other |
| promise_to_pay | `PromiseToPay \| None` {date, amount: Money} |
| callback | `Callback \| None` {datetime (tz-aware)} |
| escalated | `Escalation \| None` {reason, target} |
| dispute_flag, cease_contact_flag | `bool` |
| sentiment | `Sentiment` = positive \| neutral \| negative |
| summary | `str` (redacted) |
| kb_sources | `list[str]` (`doc_id@version#section`) |
| versions | `Versions` {prompt, policy, model} |
| latency_p50, latency_p90 | `int \| None` (ms, end-of-speech → first agent audio; filled by VOICE `LatencyTracker`) |
| latency_stages | `dict[str, StageLatency]` {p50_ms, p90_ms} per stage (stt, llm_ttft, tts_ttfb, e2e) |
| recording_uri | `str \| None` |

`busy` extends PRD §8 because F3 requires detecting it. This is noted as a deliberate addition.

Settings env names are exactly the `.env.example` keys (a test checks both directions). Field names
equal the lower-cased variable, except `postgres` and `langfuse`, which use `validation_alias`
(`settings.postgres.host` ← `POSTGRES_HOST`). The `data` group holds bank/KB/policy values (`BANK_AS_OF`,
`KB_*`, `CONTACT_RULES_PATH`, `PROMISE_MAX_DAYS`, `TRANSCRIPTS_DIR`). Required secrets (DB password,
gateway service/worker tokens, audit salt, scheduler token) have no default; CI and `make setup` copy
`.env.example`, whose placeholders are local-only. `LLM_PROVIDER=anthropic` requires `ANTHROPIC_API_KEY`.

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| Sync vs async DB | sync SQLAlchemy + psycopg 3 | low concurrency (NFR-CAP); simpler tests |
| Migrations | Alembic, one env per DB | BANK and KB evolve independently |
| Test DB isolation | session-scoped migrated `*_test` DB + per-test SAVEPOINT rollback | fast, no cross-test leakage |
| Logging | stdlib + JSON formatter | no extra dependency; easy masking filter |
| Clock | contextvar-overridable `now()` | deterministic calling-window and DPD tests |

## Dependencies
- T-PLAT-03 adds `sqlalchemy>=2.0`, `psycopg[binary]>=3.2` and `alembic>=1.13`.
- No new services: Postgres is already in compose.

## Test strategy
- **unit:**
  - `tests/common/test_settings.py`: environment overrides, missing secrets
  - `tests/common/test_logging.py`: masking, JSON shape
  - `tests/contracts/test_outcome.py`: validation, JSON-schema export is current
  - `tests/common/test_clock.py`
- **integration:** `tests/common/test_db.py` checks migrations reach head on `bank_test` and `kb_test`, plus rollback isolation.
- **tooling:** `tests/tooling/test_tasks.py` covers the spec validator, including a check that the real `specs/` tree is valid.

## Risks
- macOS Docker port clashes on 5432 with a local Postgres. Mitigation: `POSTGRES_PORT` is configurable, and `make doctor` warns about the clash.
