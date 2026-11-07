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
| Postgres 16 + pgvector, Redis, Asterisk, LiveKit SIP, Langfuse, app services | llama.cpp (Llama 3.1 8B, nomic-embed-text), whisper.cpp, Kokoro TTS, livekit-server, agent worker |

## 1. Required from day one (M0–M1)

```bash
xcode-select --install             # git, compilers
brew install uv                    # Python 3.12 toolchain; installs Python itself
brew install --cask docker         # Docker Desktop (or: brew install colima docker docker-compose && colima start)
open -a Docker                     # start the daemon
brew install node                  # Node 24+ and npm, for the Next.js console (web/console)
make setup                         # uv sync, .env from .env.example, Postgres via compose
make console-setup                 # npm ci for web/console + Playwright's Chromium (~100 MB)
make check                         # lint, types, boundaries, specs, unit + integration tests, console checks
```

**Web UIs are Next.js.** Every admin or console UI lives under `web/` (constitution VIII). The console
runs in Docker for normal use (`make console`); `make console-dev` runs it natively with hot reload.

**Postgres port clash.** Compose publishes Postgres on 5432. If another Postgres already uses that
port, `make doctor` reports `✗ postgres-port … used by another process`. Set a free port in `.env`,
for example `POSTGRES_PORT=5433`, then run `make up`. Settings, compose and the tests all read it.

**Docker resources.** Give Docker Desktop (or Colima) about 4 GB of memory. The Linux services need
about 1.5 GB, plus about 2.5 GB when Langfuse is running.

## 2. Native model servers (needed from M2)

The ports below are the defaults in `.env.example`. Change them there, not in code.

| Component | Install and start | Port | Check |
|---|---|---|---|
| llama.cpp | `brew install llama.cpp` (provides `llama-server`) | — | `llama-server --version` |
| LLM: Llama 3.1 8B Instruct (Q4_K_M, ~4.6 GB) | `llama-server -m $LLM_GGUF --alias llama-3.1-8b-instruct --jinja -c 8192 -ngl 99 --host 127.0.0.1 --port 8090` | 8090 | `curl localhost:8090/v1/models` |
| Embeddings: nomic-embed-text v1.5 (Q8_0, ~0.15 GB, 768-d) | `llama-server -m $EMBED_GGUF --alias nomic-embed-text-v1.5 --embedding --pooling mean -c 2048 -ngl 99 --host 127.0.0.1 --port 8082` | 8082 | `curl localhost:8082/v1/models` |
| whisper.cpp (STT) | `brew install whisper-cpp`, download a model (below), then `whisper-server --host 127.0.0.1 --port 8178 -m models/whisper/ggml-small.en.bin --inference-path /v1/audio/transcriptions` | 8178 | `curl localhost:8178/` |
| Kokoro-82M (TTS) | `git clone https://github.com/remsky/Kokoro-FastAPI && cd Kokoro-FastAPI && ./start-gpu_mac.sh` | 8880 | `curl localhost:8880/v1/models` |
| LiveKit server | `brew install livekit && livekit-server --dev` (dev key `devkey` / `secret`) | 7880 | `curl localhost:7880/` |

**Model files** are plain GGUF/GGML files. `LLM_GGUF` and `EMBED_GGUF` in `.env` point at them, so an
existing GGUF elsewhere on the disk is reused instead of downloaded again. By default they live in the
git-ignored `models/` folder:
- LLM: `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` (Hugging Face `bartowski/Meta-Llama-3.1-8B-Instruct-GGUF`).
  The low-memory fallback is `Llama-3.2-3B-Instruct-Q4_K_M.gguf` (~2 GB).
- Embeddings: `nomic-embed-text-v1.5.Q8_0.gguf` (`nomic-ai/nomic-embed-text-v1.5-GGUF`). It is the same
  model Ollama served as `nomic-embed-text`, so vectors stay 768-d.
- Whisper: `ggml-small.en.bin` (~0.47 GB) is the default to save disk; `ggml-large-v3-turbo.bin` (~1.6 GB)
  is optional for accuracy (`WHISPER_MODEL=large-v3-turbo`). Both come from `ggerganov/whisper.cpp`.

`--jinja` makes `llama-server` use the model's chat template, which is what enables OpenAI-style tool
calls. Llama 3.1 has no "thinking" mode, so nothing needs switching off. T-VOICE-01 replaces these
manual steps with `scripts/models/` and `make models-*`.

**Disk.** Everything above needs about 1 GB of new downloads if you already have the Llama GGUF
(add ~4.6 GB if not, ~1.5–2 GB for Kokoro's Python environment). If Docker's disk is full,
`docker builder prune` frees the build cache.

## 3. Memory budget (from the architecture)

Everything running at once uses about 13 GB, which fits in 16 GB. Start Langfuse only when you need
traces.

| Component | Memory | Note |
|---|---|---|
| Llama 3.1 8B (Q4_K_M, 8k context) in llama-server | ~5.5 GB | Llama 3.2 3B (~2.5 GB) is the fallback |
| whisper.cpp (small.en) | ~0.5 GB | large-v3-turbo ~1.6 GB if accuracy needs it |
| Kokoro TTS + embeddings model | ~0.8 GB | |
| Docker VM: Postgres, Redis, Asterisk, SIP | ~1.5 GB | |
| Langfuse stack (optional) | ~2.5 GB | `observability` compose profile (T-OBS-04) |
| Agent, gateway, bank API, console API (Python) + console (Node) | ~1.2 GB | |

If memory runs short, use the Llama 3.2 3B GGUF for the LLM, or stop Langfuse.

## 4. Optional: hosted LLM (F14)

Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=…` in `.env` to compare quality and latency
against the local model. The data is synthetic, but never put a real key in a committed file.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `✗ docker-daemon: daemon not running` | `open -a Docker` (or `colima start`) and wait for it to finish starting |
| Integration tests skip or can't connect | `make up`, then check `POSTGRES_PORT` matches between `.env` and `make doctor` |
| Databases missing after editing `infra/postgres/init/` | the init scripts run only on a fresh volume: `make reset-db` |
| Model server ✗ but it is running | check the port in `.env` (`LLM_URL`, `EMBED_URL`, `STT_URL`, `TTS_URL`, `LIVEKIT_URL`) |
