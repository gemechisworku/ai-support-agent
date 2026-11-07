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
Native model servers (llama.cpp, whisper.cpp, Kokoro, LiveKit) are needed from milestone M2. Run
`make doctor` to see what is missing, and follow [docs/dev-setup.md](docs/dev-setup.md).

## Run it locally
Everything binds to `localhost`. Ports and model paths come from `.env` (see `.env.example`).

**1. Native model servers** (macOS, Metal; one terminal tab each). Install them once with
[docs/dev-setup.md](docs/dev-setup.md). The console and gateway need only the embeddings server, for
policy search. To skip it, set `KB_EMBEDDER=fake` in `.env`.
```bash
# Embeddings (nomic-embed-text v1.5) on :8082. Used by policy search and `make kb-ingest`.
llama-server -hf nomic-ai/nomic-embed-text-v1.5-GGUF:Q8_0 --alias nomic-embed-text-v1.5 \
  --embedding --pooling mean -c 2048 -ngl 99 --host 127.0.0.1 --port 8082
# LLM (Llama 3.1 8B Instruct) on :8090. Used by the agent; -m is the GGUF path (LLM_GGUF in .env).
llama-server -m /path/to/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf --alias llama-3.1-8b-instruct --jinja -c 8192 -ngl 99 --host 127.0.0.1 --port 8090
# Speech-to-text (whisper.cpp) on :8178. Used by the voice pipeline.
whisper-server -m models/whisper/ggml-small.en.bin --host 127.0.0.1 --port 8178 \
  --inference-path /v1/audio/transcriptions
make doctor                # ✓/✗ for every prerequisite and server
```

**2. Docker services: Postgres, bank API, gateway, console API and the Next.js console.**
```bash
docker compose build bank-api console     # after code changes: the shared Python app image + the console image
docker compose up -d --wait postgres bank-api gateway console-api console   # or: make console (builds + starts all five)
docker compose ps                         # all five should be "healthy"
```

**3. Load the data** (first run, or after `make reset-db`).
```bash
docker compose exec -T bank-api python -m supportagent.bank.seed --as-of 2026-11-09 --reset   # synthetic bank
make kb-ingest             # policy documents with the real embeddings; ARGS="--embedder fake" without a model server
```
Re-running the ingest is safe: unchanged documents are skipped (`added 0 / skipped 20`).

**4. Open the console** at <http://localhost:8501>. It's the read-only operations console and data
explorer: customers, loans, the policy knowledge base, the persona test sheet and the call audit trail.
It is a Next.js app (`web/console`) that reads everything through the console API, never directly.

To work on the console with hot reload (Node 24+; `make console-setup` installs its packages once):
```bash
docker compose stop console              # frees port 8501; console-api keeps running on :8004
make console-dev                         # next dev on :8501, console API URL and token from .env
make console-check                       # lint, types, unit tests, build, e2e (also part of make check)
```
After changing a console API view model, run `make console-types` to regenerate the TypeScript types.

| Service | URL | Check |
|---|---|---|
| Console (Next.js) | <http://localhost:8501> | open in a browser |
| Console API | <http://localhost:8004> | `curl localhost:8004/healthz` · OpenAPI at `/openapi.json` |
| Bank API | <http://localhost:8001> | `curl localhost:8001/healthz` |
| Gateway (MCP at `/mcp`) | <http://localhost:8002> | `curl localhost:8002/healthz` |
| Embeddings / LLM / STT | `:8082` / `:8090` / `:8178` | `make doctor` |

**Stop or reset:** `docker compose stop` (keeps the data), `make down` (stops all services), or
`make reset-db` (deletes the local databases; then repeat step 3). Stop the model servers with
Ctrl-C in their tabs.

## Workflow in one line
Pick a ready task, write its tests, implement, mark it `done` in `tasks.md`, and commit it to the
milestone branch. Each milestone is one draft PR (`make milestone-pr M=Mn`), merged once all its tasks
are done. CI checks code quality, spec traceability and the milestone PR rules.
