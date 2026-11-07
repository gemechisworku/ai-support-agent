# Specs — how this project is built

This folder is the plan of record. It breaks the [PRD](../docs/prd.md) down into **modules** that can be
built and tested independently, following the design in [architecture.md](../docs/architecture.md),
under the rules in [constitution.md](constitution.md).

```
PRD id (F4, U2, NFR-SEC …)          docs/prd.md
  └─ requirement (GW-R3)             specs/NN-module/spec.md     WHAT + acceptance criteria
       └─ design                     specs/NN-module/plan.md     HOW: files, interfaces, data, tests
            └─ task (T-GW-04)        specs/NN-module/tasks.md    one commit-sized unit, status todo|done
                 └─ GitHub issue #N  → commit on milestone/M1 → milestone PR "Closes #N" → merged with M1
```

The workflow is a simplified [GitHub Spec Kit](https://github.com/github/spec-kit) loop
(constitution → specify → plan → tasks → implement). There is one change: specs here are per **module**,
not per feature branch, and `scripts/tasks.py` mirrors tasks to GitHub issues and milestones.
Progress and the PRD traceability matrix are generated in [STATUS.md](STATUS.md) by a bot commit on
main after every merge (`.github/workflows/status.yml`). PRs never edit it, so parallel PRs don't conflict.

## The loop (for humans and coding agents)

1. **Pick:** `make tasks-next`. It lists tasks that are `todo` and whose dependencies are all `done`.
   Prefer the lowest milestone.
2. **Read:** `python3 scripts/tasks.py show T-XXX-NN`, then the module's `spec.md` and `plan.md`, and any
   `contracts` the task touches.
3. **Branch:** the milestone branch `milestone/Mn` (one per milestone; create it from `main` the
   first time).
4. **Test first:** write the tests listed in the task (they should fail), then implement until they pass.
5. **Verify:** `make check` runs lint, types, import boundaries, unit and integration tests, and the
   specs check.
6. **Close the loop in the same commit:** set `- **Status:** done`, tick the acceptance criteria, update
   the spec or plan if reality differed. Commit `feat(<module>): … (T-XXX-NN)`, push, and run
   `make milestone-pr M=Mn` (creates the draft PR, or regenerates its body). Leave `STATUS.md` alone.
7. **Milestone complete:** when all its tasks are done, mark the PR ready. CI must be green; a human
   reviews (commit by commit) and merges it; GitHub closes every task issue it lists.

Changing the plan: add, split or re-scope tasks by editing `tasks.md` (new ids only, never reuse one),
run `make tasks-validate`, then `make issues-sync` to create the new issues.

## Modules

Package root: `src/supportagent/`. Tests mirror it under `tests/<module>/`.

| # | Code | Module | Code location | Milestones | Depends on (contracts) | PRD |
|---|---|---|---|---|---|---|
| 01 | PLAT | [Platform and contracts](01-platform/spec.md) | `common/`, `contracts/`, `infra/`, CI | M0 | — | NFR-SEC, NFR-PORT, D-OUTCOME |
| 02 | BANK | [Mock core bank](02-mock-bank/spec.md) | `bank/` | M1 | PLAT | F5, F7, F13, NFR-ACC |
| 03 | KB | [Policy knowledge base](03-knowledge-base/spec.md) | `kb/`, `data/policies/` | M1 | PLAT | F6, U4, AC-GROUND |
| 04 | GW | [Agent data gateway (MCP)](04-gateway/spec.md) | `gateway/` | M1 | BANK API, KB lib | F4, F5, F6, F7, F11, NFR-SEC |
| 05 | COMP | [Compliance rules engine](05-compliance/spec.md) | `compliance/` | M1 | PLAT | F8, AC-GUARD |
| 06 | AGENT | [Conversation agent](06-agent/spec.md) | `agent/` | M2, M3 | GW tools (MCP) | U1–U6, F2, F4, F6, F7, F9, F13 |
| 07 | VOICE | [Voice pipeline and model servers](07-voice/spec.md) | `voice/`, `scripts/models/` | M2 | PLAT | F1, F14, NFR-LAT, NFR-CAP |
| 08 | TEL | [Telephony](08-telephony/spec.md) | `telephony/`, `infra/telephony/` | M3 | VOICE, AGENT | F2, F3, F9, AC-DEMO |
| 09 | SCHED | [Outreach scheduler](09-scheduler/spec.md) | `scheduler/` | M3 | COMP lib, BANK API, LiveKit API | F3, F8, U1, U2, U5 |
| 10 | OBS | [Observability and redaction](10-observability/spec.md) | `observability/` | M2, M4 | PLAT | F11, NFR-AUDIT, NFR-SEC |
| 11 | CON | [Operations console and data explorer](11-console/spec.md) | `console/` | M2, M4 | BANK API, GW service API | F10 |
| 12 | EVAL | [Simulated-caller evals](12-evals/spec.md) | `evals/`, `tests/scenarios/` | M2, M4 | AGENT (text mode) | F12, AC-TASK, AC-GUARD, AC-GROUND, AC-EXP |

### Dependency rules (enforced by `import-linter`, see `pyproject.toml`)
- Any module may import `supportagent.common` and `supportagent.contracts`, and the two pure libraries
  `supportagent.compliance` (rules and disclosures) and `supportagent.observability` (redaction, versions).
- Other allowed cross-module imports: `gateway → kb` (policy search runs inside the gateway),
  `agent → telephony` and `scheduler → telephony` (LiveKit SIP helpers), and `evals → agent`. All other collaboration goes over HTTP (bank API, gateway service scope) or MCP
  (gateway tools).
- The gateway has no bank database connection. It calls the bank API and writes audit rows through it.
- `agent` must never import `bank`, `kb`, `gateway`, `sqlalchemy` or `psycopg` (constitution II).
- `contracts` imports nothing from the project except `common`.

### Build order and parallelism
```
M0  PLAT ───────────────┬──────────────┬───────────────┬─────────────┐
M1  BANK ──► GW ◄── KB   COMP           │               │             │
M2       AGENT (inbound) ◄─ VOICE      OBS (redaction)  EVAL (framework)
M3  TEL ──► AGENT (outbound, hand-off) ◄─ SCHED ◄─ COMP
M4  CON · OBS (Langfuse) · EVAL (suite, metrics) · tuning · demo
```
Within a milestone, modules with no arrow between them can be built in parallel. A module whose
upstream is not finished codes against the **contract** (Pydantic models in `supportagent.contracts`)
and fakes it in tests.

## Shared conventions

| Topic | Convention |
|---|---|
| Python | 3.12, `uv`, `src/` layout, single distribution `supportagent` |
| Config | `supportagent.common.settings` (pydantic-settings, `.env`); no `os.environ` elsewhere |
| Databases | Postgres 16 + pgvector; databases `bank` and `kb`; Alembic migrations per database |
| Ports | Postgres 5432 · Redis 6379 · bank API 8001 · gateway 8002 (`/mcp`, `/sessions`, `/service`) · scheduler API 8003 · console 8501 · LiveKit 7880 (UDP 50000–50099) · LiveKit SIP 5070 (RTP 10000–10099) · Asterisk 5060 (RTP 20000–20099) · llama.cpp LLM 8090 · llama.cpp embeddings 8082 · whisper.cpp 8178 · Kokoro 8880 · Langfuse 3000 |
| Money | `Decimal` (2 dp) in code, `NUMERIC(12,2)` in SQL; never float |
| Time | timezone-aware UTC in storage; customer local time only for calling-window checks |
| Ids | `call_id` UUID4 string; bank ids are integers; masked numbers show the last 4 only |
| Tests | `pytest`; markers `unit` (default), `integration`, `eval`, `live`; fakes over mocks |
| Logging | structured JSON via `supportagent.common.logging`; never log raw PII |

## Templates
New module: copy [`_templates/`](_templates/) into `specs/NN-name/` and register it in the table above.
