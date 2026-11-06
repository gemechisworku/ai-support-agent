# AGENTS.md

This file is the operating manual for coding agents and humans working in this repo. Claude Code
loads it through [CLAUDE.md](CLAUDE.md).

## What this is
This is a proof-of-concept **AI voice agent for loan servicing**. It makes outbound reminder and
collections calls and handles inbound support. It verifies identity, reads data through a secured MCP
gateway, answers policy questions from a RAG knowledge base, captures structured outcomes and hands
calls off to humans. Everything runs locally on one Mac (M4, 16 GB) against a synthetic mock bank.

- **What to build:** [docs/prd.md](docs/prd.md)
- **How it fits together:** [docs/architecture.md](docs/architecture.md)
- **Rules that never bend:** [specs/constitution.md](specs/constitution.md)
- **Work breakdown, workflow and conventions:** [specs/README.md](specs/README.md)
- **Progress:** [specs/STATUS.md](specs/STATUS.md)

## Work only from a task
All work maps to a task `T-XXX-NN` in `specs/NN-module/tasks.md`. Each task is one GitHub issue and
**one commit**. Each **milestone** is one branch (`milestone/Mn`) and one draft PR that collects the
commits of all its tasks; it is marked ready and merged as a whole when every task in it is done.

```bash
make tasks-next                          # ready tasks (deps done), lowest milestone first
python3 scripts/tasks.py show T-GW-05    # task + requirement text + spec/plan paths
```

For the chosen task:
1. Read the task, its module's `spec.md` (requirements and acceptance criteria) and its `plan.md`
   (file layout, contracts, decisions). Read the upstream module's spec **Interfaces** section for
   anything you consume.
2. Work on the task's milestone branch: `git switch milestone/Mn` (create it from an up-to-date `main`
   with `git switch -c milestone/Mn origin/main` if it doesn't exist yet; otherwise `git pull`).
3. Write the task's listed tests first, then implement until they pass. Stay inside the task's scope.
   If you find needed work outside it, add a new task to the right `tasks.md` instead of doing it.
4. Run `make check` until it is green.
5. In the same commit:
   - set `- **Status:** done`
   - tick the acceptance criteria
   - fix `spec.md` / `plan.md` if the implementation differs
6. Commit as `feat(<module>): <summary> (T-XXX-NN)` (one commit per task; follow-up fixes to a task
   say `fix(<module>): … (T-XXX-NN)`). Push the milestone branch, then run `make milestone-pr M=Mn`:
   it creates the draft PR the first time and regenerates its body (task checklist + `Closes #N`
   for every done task) every time after.
7. When every task of the milestone is done, mark the PR ready (`gh pr ready`). A human reviews and
   merges it. Never merge it yourself.

Never mark a task done if its tests are skipped, xfailed or missing.

## Definition of done
- [ ] Every test named in the task exists and passes, along with the rest of `make check`: lint,
      mypy, import boundaries, specs and traceability, unit and integration tests.
- [ ] The task's acceptance criteria are ticked, and its Status is `done`.
- [ ] `spec.md` and `plan.md` match what was built. New dependencies and services are listed in `plan.md`.
- [ ] Any new config is in `.env.example`. Don't edit `specs/STATUS.md`: a bot regenerates it on main
      after each merge, and CI rejects PRs that touch it (so parallel PRs never conflict).
- [ ] CI is green on the milestone PR. CI rejects: `task/*` branches, a milestone PR body that is out of
      date, and a milestone PR marked ready while any of its tasks is still `todo`.

## Commands
```bash
make setup         # uv sync, create .env, start Postgres
make up / down     # docker compose services
make test          # unit tests (no services)
make test-int      # integration tests (Postgres)
make check         # everything CI runs
make docker-test   # tests inside Docker
make fmt           # ruff format + autofix
make tasks-validate | tasks-status (print only) | tasks-next
make issues-sync   # mirror tasks → GitHub issues/milestones (writes issue numbers back)
make help          # everything else
```
`scripts/tasks.py` needs only the system `python3`. Everything else runs through `uv`.

## Architecture rules you must not break
These are checked by CI or by tests. Details are in the constitution.
- **Data boundary:** `supportagent.agent` and `supportagent.voice` never import `bank`, `kb`,
  `gateway`, `sqlalchemy` or `psycopg`. The agent gets data only from gateway MCP tools.
- **No customer id from the model:** the gateway binds identity after `verify_identity`. Tools take
  no `customer_id` argument after binding.
- **Guardrails are code:** verification-before-data, disclosures, calling window, frequency cap, DNC,
  consent, dispute and cease-contact each have a deterministic code path and a unit test. A prompt
  alone is never enough.
- **Grounding:** amounts and dates come from tool results, and policy text comes from retrieved
  chunks (log `doc_id@version`). Below the threshold, the agent offers a follow-up.
- **Agent writes are limited:** the agent writes interactions, promises, tasks, contact flags and the
  outbox, and never balances.
- **Contracts:** cross-module types live in `supportagent.contracts`. Change a contract only in the
  task that owns it, or in a task that updates every consumer.

## Code conventions
- Python 3.12, type hints everywhere (mypy `disallow_untyped_defs`), ruff with line length 110.
- Config comes only from `supportagent.common.settings`. Log only through
  `supportagent.common.logging` and never log raw PII.
- Money is `Decimal`, never float. Timestamps are timezone-aware UTC. Customer local time is used
  only for calling-window checks.
- Prefer small pure functions plus thin adapters, such as an HTTP route or MCP tool around a pure
  core. That keeps unit tests free of I/O.
- Tests: prefer fakes over mocks: fake gateway, fake embedder, scripted LLM, `httpx.MockTransport`.
  Unmarked tests are unit tests and must not touch the network. Use the `integration` marker for
  Postgres, `eval` for a real LLM and `live` for model servers and telephony.
- All data is synthetic. Never add real names, numbers or credentials, even in tests.

## Where things run
| Runs in Docker (`docker compose`) | Runs natively on macOS (Metal GPU) |
|---|---|
| Postgres + pgvector, Redis, Asterisk, LiveKit SIP, Langfuse, app services (bank API, gateway, scheduler, console) | Ollama (Qwen3 8B, nomic-embed-text), whisper.cpp, Kokoro TTS, livekit-server, the agent worker |

Unit and integration tests never need the native column.

## Gotchas
- Port 5432 may already be used by a local Postgres. Set `POSTGRES_PORT` in `.env`.
- The integration DBs are `bank_test` and `kb_test`, created by `infra/postgres/init/`. If you added
  init SQL after the volume existed, run `make reset-db`.
- Ids in `tasks.md` are permanent: don't renumber or reuse them. To drop a task, re-scope it in place
  and say so in the PR.
- Only change the field-bullet format in `tasks.md` and the requirement-line format in `spec.md`
  together with `scripts/tasks.py` and its tests.
