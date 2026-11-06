# 01 · Platform and contracts — Tasks

> Each task is one GitHub issue and one PR. The field bullets are parsed by `scripts/tasks.py`, so keep
> their exact format. Status is `todo` or `done`. Issue is `—` until `make issues-sync` fills it in.

## Tasks

### T-PLAT-01 · Repository bootstrap: toolchain, Docker, CI/CD, spec workflow
- **Status:** done
- **Milestone:** M0
- **Issue:** #80
- **Depends on:** —
- **Requirements:** PLAT-R5

Bootstrap the repository: the uv project, ruff, mypy, pytest markers, import-linter boundaries, the
compose file with Postgres + pgvector, a single app Dockerfile (runtime and test targets), the
Makefile, CI and release workflows, the issue and PR templates, AGENTS.md and CLAUDE.md, and the spec
workflow (`specs/`, `scripts/tasks.py`). Done in the initial commit.

**Acceptance criteria**
- [x] `make check` passes on a clean clone
- [x] `docker compose up -d --wait postgres` creates the databases `bank`, `kb`, `bank_test` and `kb_test` (with pgvector in the kb databases)
- [x] CI runs the specs, lint, test and docker jobs; release publishes to GHCR after a green CI on main
- [x] `python3 scripts/tasks.py validate` passes

**Tests**
- `tests/test_smoke.py` (unit): every module package imports
- `tests/tooling/test_tasks.py` (unit): the spec validator, traceability, `check-pr`, issue write-back, and that the real repo validates

**Files:** `pyproject.toml`, `uv.lock`, `Makefile`, `docker-compose.yml`, `docker/app.Dockerfile`, `infra/postgres/init/`, `.github/`, `scripts/tasks.py`, `specs/`, `AGENTS.md`, `CLAUDE.md`

### T-PLAT-02 · Typed settings and PII-safe structured logging
- **Status:** done
- **Milestone:** M0
- **Issue:** #1
- **Depends on:** T-PLAT-01
- **Requirements:** PLAT-R1, PLAT-R2

Implement `common/settings.py` and `common/logging.py` as in [plan.md](plan.md#file-layout). Settings
cover every variable in `.env.example`, grouped (`postgres`, `data`, `services`, `models`,
`livekit`, `langfuse`, `persona`). Logging emits one JSON object per line and masks PII keys recursively.

**Acceptance criteria**
- [x] `get_settings()` is cached, and `get_settings.cache_clear()` is used by the test fixture
- [x] Every `.env.example` key maps to a settings field (a test parses `.env.example`)
- [x] `postgres.dsn("bank")` builds a psycopg URL
- [x] PII keys are masked recursively in dicts and lists; phone and number fields keep only the last 4 characters
- [x] `bind_call(call_id)` adds `call_id` to every log record in that context

**Tests**
- `tests/common/test_settings.py` (unit): env override, `.env.example` coverage, DSN building, missing-secret error
- `tests/common/test_logging.py` (unit): masking of nested PII, JSON shape, `call_id` binding, unserialisable values don't raise

**Files:** `src/supportagent/common/settings.py`, `src/supportagent/common/logging.py`, `tests/common/`

### T-PLAT-03 · Database plumbing and Alembic for bank and kb
- **Status:** todo
- **Milestone:** M0
- **Issue:** #2
- **Depends on:** T-PLAT-02
- **Requirements:** PLAT-R3

Add SQLAlchemy 2.0, psycopg 3 and Alembic. Create `common/db.py` (engines, `session_scope`,
`BankBase`, `KbBase`) and two Alembic environments with an empty baseline revision each, plus
`make migrate` / `make migrate-test`.

**Acceptance criteria**
- [ ] `make migrate` upgrades both `bank` and `kb` to head against compose Postgres
- [ ] `alembic -n kb upgrade head` works on its own (independent environments)
- [ ] `session_scope` commits on success and rolls back on exception
- [ ] CI's test job runs migrations against `bank_test` and `kb_test` before pytest (via fixtures)

**Tests**
- `tests/common/test_db.py` (integration): upgrade/downgrade both databases; `session_scope` commit and rollback

**Files:** `src/supportagent/common/db.py`, `alembic.ini`, `migrations/bank/`, `migrations/kb/`, `Makefile`, `pyproject.toml`

### T-PLAT-04 · Shared contracts: CallOutcome and common enums
- **Status:** done
- **Milestone:** M0
- **Issue:** #3
- **Depends on:** T-PLAT-01
- **Requirements:** PLAT-R4

Implement `contracts/outcome.py` and `contracts/money.py` exactly as in the
[plan](plan.md#data-model--contracts). Export the JSON schema to
`docs/contracts/call_outcome.schema.json` with `python -m supportagent.contracts.export`.

**Acceptance criteria**
- [x] Every PRD §8 field is present with the planned type
- [x] `promise_to_pay` requires `amount > 0`, and `callback.datetime` must be timezone-aware
- [x] `Money` rejects floats with more than 2 dp and serialises as a string
- [x] The JSON schema export is checked in, and a test fails if it is stale

**Tests**
- `tests/contracts/test_outcome.py` (unit): round-trip, enum rejection, promise/callback validation, schema export is current

**Files:** `src/supportagent/contracts/outcome.py`, `src/supportagent/contracts/money.py`, `src/supportagent/contracts/export.py`, `docs/contracts/`

### T-PLAT-05 · Test infrastructure: DB fixtures, frozen clock, factories
- **Status:** todo
- **Milestone:** M0
- **Issue:** #4
- **Depends on:** T-PLAT-03
- **Requirements:** PLAT-R6

Write `tests/conftest.py` with fixtures:
- `settings`: test env pointed at the `*_test` databases
- `bank_db` and `kb_db`: session-scoped migration, then a per-test connection-level transaction with a SAVEPOINT that is rolled back
- `frozen_clock`

Add `common/clock.py`. Integration fixtures skip with a clear message when Postgres is unreachable,
unless `CI=true`, in which case they fail.

**Acceptance criteria**
- [ ] Two tests inserting the same unique row both pass (isolation)
- [ ] Without Postgres, `pytest -m integration` skips locally and fails in CI
- [ ] `freeze_clock` works as a context manager and as a fixture, and `clock.today()` follows it

**Tests**
- `tests/common/test_clock.py` (unit): freeze and advance
- `tests/common/test_fixtures.py` (integration): rollback isolation for `bank_db` and `kb_db`

**Files:** `tests/conftest.py`, `src/supportagent/common/clock.py`, `tests/common/`

### T-PLAT-06 · make doctor and native dev-setup guide
- **Status:** todo
- **Milestone:** M0
- **Issue:** #5
- **Depends on:** T-PLAT-01
- **Requirements:** PLAT-R7

Add `scripts/doctor.sh` and a `make doctor` target. It checks uv, Docker and Compose, the Postgres
port, Ollama (and that `qwen3:8b` and `nomic-embed-text` are pulled), whisper.cpp on :8178, Kokoro on
:8880 and livekit-server on :7880. Each check prints ✓ or ✗ with a fix hint. Write
`docs/dev-setup.md` covering the native (Metal) components that Docker cannot run on macOS.

**Acceptance criteria**
- [ ] `make doctor` exits 0 only when every **required** check passes; model servers are reported as optional until M2
- [ ] Each ✗ line names the exact install or start command
- [ ] `docs/dev-setup.md` covers first-time setup on a 16 GB M4 Mac, including the memory budget from the architecture

**Tests**
- `tests/tooling/test_doctor.py` (unit): runs `scripts/doctor.sh --dry-run`, which lists the checks without probing them, and asserts every check is present

**Files:** `scripts/doctor.sh`, `docs/dev-setup.md`, `Makefile`, `tests/tooling/test_doctor.py`
