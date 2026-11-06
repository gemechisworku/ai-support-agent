# AI Voice Support Agent — loan servicing POC

[![CI](https://github.com/gemechisworku/ai-support-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/gemechisworku/ai-support-agent/actions/workflows/ci.yml)
[![Release](https://github.com/gemechisworku/ai-support-agent/actions/workflows/release.yml/badge.svg)](https://github.com/gemechisworku/ai-support-agent/actions/workflows/release.yml)
[Issues by milestone](https://github.com/gemechisworku/ai-support-agent/milestones) · [Status](specs/STATUS.md)

This is a voice AI agent for a bank. It calls customers about upcoming and missed loan payments,
answers first-level support calls, and hands off to humans when needed. It runs fully locally on one
Mac, with open-source components and a synthetic mock bank.

| Read | For |
|---|---|
| [docs/prd.md](docs/prd.md) | What we are building, and the acceptance criteria |
| [docs/architecture.md](docs/architecture.md) | Components, flows, infrastructure |
| [specs/README.md](specs/README.md) | Modules, milestones, and the spec → task → issue → PR workflow |
| [specs/STATUS.md](specs/STATUS.md) | Live progress and PRD traceability matrix |
| [AGENTS.md](AGENTS.md) | Rules and the Definition of Done for coding agents (and humans) |

The original signed-off documents (HTML/PDF) are in [docs/](docs/) and [docs/design/](docs/design/).

## Quick start
```bash
brew install uv            # Python toolchain (Docker Desktop or Colima also required)
make setup                 # deps, .env, Postgres (pgvector) via docker compose
make check                 # lint, types, import boundaries, specs, unit + integration tests
make tasks-next            # what to build next
```
Native model servers (Ollama, whisper.cpp, Kokoro, LiveKit) are needed from milestone M2. See
`make doctor` and `docs/dev-setup.md` (added by T-PLAT-06).

## Workflow in one line
Pick a ready task, write its tests, implement, mark it `done` in `tasks.md`, then open a PR that says
`Closes #<issue>`. CI checks code quality and spec traceability.
