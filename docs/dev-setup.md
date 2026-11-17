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

Three Make targets wrap `scripts/models/` (T-VOICE-01). They read every URL, port, model alias and file
path from `.env`, and every server binds to 127.0.0.1.

```bash
make models-setup                  # brew deps, missing model files, Kokoro-FastAPI (idempotent)
make models-setup ARGS=--dry-run   # print what it would install or download, and the disk needed
make models-up                     # start all five in the background (ARGS="llm embed" for a subset)
make models-health                 # ✓/✗ per server; exit 1 names the failing one
make models-down                   # stop what models-up started
```

- **Setup:** `models-setup` installs the brew formulae `llama.cpp`, `whisper-cpp`, `livekit` and
  `livekit-cli`, skipping any that are already installed. It downloads a GGUF only when the file at
  `LLM_GGUF` / `EMBED_GGUF` is missing. If the Hugging Face cache already holds that file (for example,
  from `llama-server -hf …`), it links to the cached copy instead. Whisper files are skipped when their
  sha256 matches. It clones Kokoro-FastAPI (`KOKORO_REF`, default `v0.9.2`) into the git-ignored
  `vendor/` and prepares its Python environment. Extras: `--with-fallbacks` adds Llama 3.2 3B, and
  `--with-large-stt` adds whisper large-v3-turbo.
- **Starting servers:** `models-up` writes pids to `var/run/` and logs to `var/log/`. It leaves alone
  any server whose port already answers, so servers you started by hand keep running, and
  `models-down` does not stop them.

| Server | Port (`.env`) | Started as | Memory |
|---|---|---|---|
| LLM: Llama 3.1 8B Instruct Q4_K_M (~4.9 GB file) | 8090 (`LLM_URL`) | `llama-server -m $LLM_GGUF --alias $LLM_MODEL --jinja -c 8192 -ngl 99` | ~5.5 GB |
| Embeddings: nomic-embed-text v1.5 Q8_0 (~0.15 GB, 768-d) | 8082 (`EMBED_URL`) | `llama-server -m $EMBED_GGUF --alias $EMBED_MODEL --embedding --pooling mean -c 2048 -ngl 99` | ~0.3 GB |
| STT: whisper.cpp, `ggml-small.en.bin` (~0.49 GB) | 8178 (`STT_URL`) | `whisper-server -m models/whisper/ggml-$WHISPER_MODEL.bin --inference-path /v1/audio/transcriptions` | ~0.5 GB (large-v3-turbo ~1.6 GB) |
| TTS: Kokoro-82M (Kokoro-FastAPI) | 8880 (`TTS_URL`) | `HOST=127.0.0.1 PORT=8880 ./start-gpu_mac.sh` in `vendor/kokoro-fastapi` | ~0.5 GB |
| LiveKit server (dev key `devkey` / `secret`) | 7880 (`LIVEKIT_URL`) | `livekit-server --dev --bind 127.0.0.1` | ~0.1 GB |

**Model files** are plain GGUF/GGML files. `LLM_GGUF` and `EMBED_GGUF` in `.env` point at them, so an
existing GGUF elsewhere on the disk is reused instead of downloaded again. By default they live in the
git-ignored `models/` folder:
- LLM: `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` (Hugging Face `bartowski/Meta-Llama-3.1-8B-Instruct-GGUF`).
  The low-memory fallback is `Llama-3.2-3B-Instruct-Q4_K_M.gguf` (~2 GB).
- Embeddings: `nomic-embed-text-v1.5.Q8_0.gguf` (`nomic-ai/nomic-embed-text-v1.5-GGUF`). It is the same
  model Ollama served as `nomic-embed-text`, so vectors stay 768-d.
- Whisper: `ggml-small.en.bin` is the default to save disk; `ggml-large-v3-turbo.bin` is optional for
  accuracy (`WHISPER_MODEL=large-v3-turbo`). Both come from `ggerganov/whisper.cpp`.

`--jinja` makes `llama-server` use the model's chat template, which is what enables OpenAI-style tool
calls. Llama 3.1 has no "thinking" mode, so nothing needs switching off.

**Use `127.0.0.1`, not `localhost`, in the model URLs.** On macOS, `localhost` resolves to IPv6 `::1`
first. Another program listening on `[::1]:<port>` (an `ssh -L` tunnel, for example) then receives the
request instead of the server bound to 127.0.0.1. `.env.example` and the settings defaults already use
127.0.0.1.

**Disk.** Everything above needs about 1 GB of new downloads if you already have the Llama GGUF
(add ~4.9 GB if not, ~2 GB for Kokoro's Python environment). `make models-setup ARGS=--dry-run` prints
the exact amount. If Docker's disk is full, `docker builder prune` frees the build cache.

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
| Model server ✗ but it is running | check the port in `.env` (`LLM_URL`, `EMBED_URL`, `STT_URL`, `TTS_URL`, `LIVEKIT_URL`); `make models-health` names the failing probe |
| `HTTP 403` (or another unexpected server) on a model URL | something else listens on `[::1]` at that port: use `127.0.0.1` in the URL (see §2); `lsof -nP -iTCP:<port> -sTCP:LISTEN` shows who |
| `make models-up` says a server exited | read `var/log/<name>.log`; a missing model file means `make models-setup` |
