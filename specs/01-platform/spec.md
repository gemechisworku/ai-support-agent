# 01 · Platform and contracts — Spec

> **Code:** `PLAT` · **Location:** `src/supportagent/common/`, `src/supportagent/contracts/`, `infra/`,
> `docker/`, `.github/`, `scripts/` · **Milestones:** M0
> **PRD:** NFR-SEC, NFR-PORT, NFR-ACC, D-OUTCOME, F7 · **Depends on:** — · **Status:** see [tasks.md](tasks.md)

## Purpose
This module provides the shared foundation every other module builds on:
- one typed configuration object
- PII-safe logging
- database engines and migrations for the `bank` and `kb` databases
- the shared contract models (above all `CallOutcome`, PRD §8)
- test fixtures
- the developer environment: Docker Compose, Make, CI/CD and `make doctor`

It contains no business logic. Domain tables belong to their modules: BANK owns the `bank` schema and
KB owns the `kb` schema.

## Scope
**In:**
- `supportagent.common` (settings, logging, db, clock)
- `supportagent.contracts.outcome` and the shared enums
- pytest fixtures and markers
- docker-compose base services, the Dockerfile, CI and release workflows
- the spec tooling (`scripts/tasks.py`)
- `make doctor`

**Out:**
- domain tables: BANK and KB own them
- native model servers: VOICE
- telephony containers: TEL
- Langfuse: OBS

## Requirements
- **PLAT-R1** (P0 · NFR-SEC, NFR-PORT) — All configuration shall be read from environment variables / `.env` through one typed `Settings` object (`supportagent.common.settings.get_settings()`); no module reads `os.environ` directly and no secret has a default value other than an obvious local-only placeholder.
- **PLAT-R2** (P0 · NFR-SEC) — The platform shall provide structured JSON logging that masks PII-bearing fields by key (`full_name`, `name`, `dob`, `phone`, `email`, `address`, `account_number`, `loan_number`, `national_id`) before a record is emitted.
- **PLAT-R3** (P0 · NFR-PORT) — The platform shall provide SQLAlchemy 2.0 engine/session factories for the `bank` and `kb` databases and an Alembic setup with one migration environment per database, runnable with `make migrate`.
- **PLAT-R4** (P0 · D-OUTCOME, F7) — The platform shall define the shared contracts `CallOutcome` (every field in PRD §8) and the enums `Direction`, `Campaign` (incl. `playground` for no-telephony dev calls), `ContactResult`, `ReasonCode`, `Sentiment`, `Intent`, with validation (e.g. a `promise_to_pay` requires date and amount > 0; money is `Decimal` with 2 dp).
- **PLAT-R5** (P0 · NFR-PORT) — The repository shall start all Linux services with one `docker compose up`, expose all workflows as `make` targets, and run CI (specs check, lint, types, import boundaries, unit + integration tests, image build) on every PR; green pushes to `main` publish the service image to GHCR.
- **PLAT-R6** (P0 · NFR-ACC) — The platform shall provide test infrastructure: an isolated, migrated Postgres database per test session with per-test transaction rollback, a controllable clock (`supportagent.common.clock`), and pytest markers `integration`, `eval`, `live`.
- **PLAT-R7** (P1 · NFR-PORT) — `make doctor` shall check the native prerequisites (uv, Docker, Ollama and models, whisper.cpp server, Kokoro, livekit-server) and print a fix hint for each missing one.

## Acceptance criteria
- **PLAT-R1:** Given `.env` with `POSTGRES_PORT=6543`, `get_settings().postgres.port == 6543`. A missing required secret raises a clear `ValidationError` at startup. `grep -r "os.environ" src/` finds only `common/settings.py`.
- **PLAT-R2:** Logging `{"phone": "+15551234567", "loan_id": 7}` emits `"phone": "***4567"` and keeps `loan_id`. The output is valid JSON with `ts`, `level`, `logger`, `msg`, `call_id` (if bound).
- **PLAT-R3:** `make migrate` on a fresh compose Postgres upgrades both databases to head. `alembic -n bank downgrade base` works.
- **PLAT-R4:** `CallOutcome.model_validate(example_json)` round-trips. An unknown `contact_result` fails validation. `CallOutcome.model_json_schema()` is exported to `docs/contracts/call_outcome.schema.json`, and CI checks that the export is current.
- **PLAT-R5:** On a clean clone, `make setup && make check` passes. A PR that breaks lint, types, boundaries, tests or specs fails CI.
- **PLAT-R6:** Two integration tests that insert the same unique row both pass, which proves per-test rollback. `freeze_clock("2026-11-09T14:00:00Z")` controls `clock.now()`.
- **PLAT-R7:** With Ollama stopped, `make doctor` prints `✗ ollama: not reachable on http://localhost:11434 (optional until M2) → brew install ollama && brew services start ollama`. It exits 0 while every required check (uv, Docker, Compose, Postgres port) passes, and `make doctor ARGS=--strict` (from M2 on) exits non-zero.

## Interfaces
**Provides:**
- `supportagent.common.settings`: `Settings` and `get_settings()`, cached and overridable in tests.
- `supportagent.common.logging`: `configure_logging()`, `get_logger(name)` and `bind_call(call_id)`, a context variable.
- `supportagent.common.db`: `bank_engine()`, `kb_engine()`, `session_scope(engine)` and `Base` classes `BankBase` and `KbBase`.
- `supportagent.common.clock`: `now()`, `today()`, plus `freeze_clock()` for tests.
- `supportagent.contracts.outcome`: `CallOutcome`, `PromiseToPay`, `Callback`, `Escalation`, `Versions`, `StageLatency` and the enums.
- `tests/conftest.py` fixtures: `settings`, `bank_db`, `kb_db`, `frozen_clock`.

**Consumes:** nothing in the project.

## Non-functional
- Unit tests start in < 2 s and need no network.
- Logging never raises on unserialisable values.
- Only `pydantic` and `pydantic-settings` are runtime dependencies at M0. SQLAlchemy, Alembic and psycopg are added by T-PLAT-03.

## Open questions
- None blocking. Jurisdiction stays US placeholder via config (PRD §12).
