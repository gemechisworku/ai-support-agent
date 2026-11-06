# Developer setup (16 GB Apple Silicon Mac)

This guide takes a fresh Mac (M4, 16 GB, macOS) to a working POC environment. Run `make doctor` at
any point: it lists every prerequisite with ✓ or ✗, and each ✗ names the command that fixes it.

```bash
make doctor             # required checks must pass; model servers are optional until M2
make doctor ARGS=--strict   # from M2 on: model servers are required too
```

## Where things run

Docker on macOS cannot use the GPU, so the model servers run **natively** on Metal. Everything else
runs in Docker (see [architecture.md §6](architecture.md#6-infrastructure)).

| Docker (`docker compose`) | Native macOS (Metal) |
|---|---|
| Postgres 16 + pgvector, Redis, Asterisk, LiveKit SIP, Langfuse, app services | Ollama (Qwen3 8B, nomic-embed-text), whisper.cpp, Kokoro TTS, livekit-server, agent worker |

## 1. Required from day one (M0–M1)

```bash
xcode-select --install             # git, compilers
brew install uv                    # Python 3.12 toolchain; installs Python itself
brew install --cask docker         # Docker Desktop (or: brew install colima docker docker-compose && colima start)
open -a Docker                     # start the daemon
make setup                         # uv sync, .env from .env.example, Postgres via compose
make check                         # lint, types, boundaries, specs, unit + integration tests
```

**Postgres port clash.** Compose publishes Postgres on 5432. If another Postgres already uses that
port, `make doctor` reports `✗ postgres-port … used by another process`. Set a free port in `.env`,
for example `POSTGRES_PORT=5433`, then run `make up`. Settings, compose and the tests all read it.

**Docker resources.** Give Docker Desktop (or Colima) about 4 GB of memory. The Linux services need
about 1.5 GB, plus about 2.5 GB when Langfuse is running.

## 2. Native model servers (needed from M2)

The ports below are the defaults in `.env.example`. Change them there, not in code.

| Component | Install and start | Port | Check |
|---|---|---|---|
| Ollama (LLM + embeddings) | `brew install ollama && brew services start ollama` | 11434 | `curl localhost:11434/api/tags` |
| Qwen3 8B (Q4_K_M) | `ollama pull qwen3:8b` (fallback: `qwen3:4b`) | — | listed by `ollama list` |
| nomic-embed-text (768-d) | `ollama pull nomic-embed-text` | — | listed by `ollama list` |
| whisper.cpp (STT) | `brew install whisper-cpp`, download a model (below), then `whisper-server --host 127.0.0.1 --port 8178 -m models/ggml-large-v3-turbo.bin` | 8178 | `curl localhost:8178/` |
| Kokoro-82M (TTS) | `git clone https://github.com/remsky/Kokoro-FastAPI && cd Kokoro-FastAPI && ./start-gpu_mac.sh` | 8880 | `curl localhost:8880/v1/models` |
| LiveKit server | `brew install livekit && livekit-server --dev` (dev key `devkey` / `secret`) | 7880 | `curl localhost:7880/` |

**Whisper models** go in the git-ignored `models/` folder. Use `large-v3-turbo` for accuracy. If
latency is tight, use `small.en` and set `WHISPER_MODEL=small.en` in `.env`. Models can be downloaded
from the whisper.cpp model repository (`ggml-large-v3-turbo.bin`, `ggml-small.en.bin`). T-VOICE-01
replaces these manual steps with `scripts/models/` and `make models`.

**Qwen3 "thinking" mode** is disabled by the agent for latency (T-VOICE-03). Nothing needs
configuring in Ollama.

## 3. Memory budget (from the architecture)

Everything running at once uses about 13 GB, which fits in 16 GB. Start Langfuse only when you need
traces.

| Component | Memory | Note |
|---|---|---|
| Qwen3 8B (Q4_K_M) in Ollama | ~5.5 GB | Qwen3 4B is the fallback |
| whisper.cpp (large-v3-turbo) | ~1.6 GB | small.en ~0.5 GB if latency is tight |
| Kokoro TTS + embeddings model | ~0.8 GB | |
| Docker VM: Postgres, Redis, Asterisk, SIP | ~1.5 GB | |
| Langfuse stack (optional) | ~2.5 GB | `observability` compose profile (T-OBS-04) |
| Agent, gateway, API, console (Python) | ~1 GB | |

If memory runs short, use `small.en` for STT and `qwen3:4b` for the LLM, or stop Langfuse.

## 4. Optional: hosted LLM (F14)

Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=…` in `.env` to compare quality and latency
against the local model. The data is synthetic, but never put a real key in a committed file.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `✗ docker-daemon: daemon not running` | `open -a Docker` (or `colima start`) and wait for it to finish starting |
| Integration tests skip or can't connect | `make up`, then check `POSTGRES_PORT` matches between `.env` and `make doctor` |
| Databases missing after editing `infra/postgres/init/` | the init scripts run only on a fresh volume: `make reset-db` |
| Model server ✗ but it is running | check the port in `.env` (`OLLAMA_URL`, `STT_URL`, `TTS_URL`, `LIVEKIT_URL`) |
