# Shared helpers for scripts/models/*.sh (sourced, not executed). macOS bash 3.2 compatible.
# Settings come from the environment, else .env, else the .env.example default — like scripts/doctor.sh.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_DIR="$ROOT/var/run"
LOG_DIR="$ROOT/var/log"

setting() {
  local key="$1" default="$2" value
  value="$(printenv "$key" 2>/dev/null || true)"
  if [ -z "$value" ] && [ -f "$ROOT/.env" ]; then
    value="$(grep -E "^${key}=" "$ROOT/.env" | tail -n 1 | cut -d= -f2- | sed -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
  fi
  printf '%s' "${value:-$default}"
}

# Port of a URL such as http://127.0.0.1:8090/v1 or ws://127.0.0.1:7880.
port_of() { printf '%s' "$1" | sed -E 's#^[a-z]+://[^:/]+:([0-9]+).*#\1#'; }

# Relative paths in .env are relative to the repo root.
abs_path() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s' "$ROOT/$1" ;; esac; }

LLM_URL="$(setting LLM_URL http://127.0.0.1:8090/v1)"
EMBED_URL="$(setting EMBED_URL http://127.0.0.1:8082/v1)"
LLM_MODEL="$(setting LLM_MODEL llama-3.1-8b-instruct)"
EMBED_MODEL="$(setting EMBED_MODEL nomic-embed-text-v1.5)"
LLM_GGUF="$(abs_path "$(setting LLM_GGUF models/llm/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf)")"
EMBED_GGUF="$(abs_path "$(setting EMBED_GGUF models/embed/nomic-embed-text-v1.5.Q8_0.gguf)")"
STT_URL="$(setting STT_URL http://127.0.0.1:8178)"
WHISPER_MODEL="$(setting WHISPER_MODEL small.en)"
TTS_URL="$(setting TTS_URL http://127.0.0.1:8880)"
LIVEKIT_URL="$(setting LIVEKIT_URL ws://127.0.0.1:7880)"
KOKORO_DIR="$ROOT/vendor/kokoro-fastapi"
WHISPER_BIN="$ROOT/models/whisper/ggml-$WHISPER_MODEL.bin"
