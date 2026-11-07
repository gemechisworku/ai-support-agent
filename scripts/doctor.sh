#!/usr/bin/env bash
# make doctor — check the local prerequisites (PLAT-R7). See docs/dev-setup.md.
#
#   scripts/doctor.sh            run all checks; exit 1 if a REQUIRED check fails
#   scripts/doctor.sh --strict   also fail on optional checks (use from milestone M2 on)
#   scripts/doctor.sh --dry-run  list the checks without probing anything
#
# Required: uv, Docker (CLI + running daemon), Docker Compose, a usable Postgres port.
# Optional until M2: the native Metal model servers (llama.cpp LLM + embeddings, whisper.cpp, Kokoro, LiveKit).
# Written for macOS's bash 3.2: no associative arrays, no GNU-only flags; every probe has a short timeout.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRY=0
STRICT=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --strict) STRICT=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# Value of KEY from the environment, else from .env, else the default (matches .env.example).
setting() {
  local key="$1" default="$2" value
  value="$(printenv "$key" 2>/dev/null || true)"
  if [ -z "$value" ] && [ -f "$ROOT/.env" ]; then
    value="$(grep -E "^${key}=" "$ROOT/.env" | tail -n 1 | cut -d= -f2- | sed -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
  fi
  printf '%s' "${value:-$default}"
}

POSTGRES_PORT="$(setting POSTGRES_PORT 5432)"
LLM_URL="$(setting LLM_URL http://localhost:8081/v1)"
EMBED_URL="$(setting EMBED_URL http://localhost:8082/v1)"
LLM_MODEL="$(setting LLM_MODEL llama-3.1-8b-instruct)"
EMBED_MODEL="$(setting EMBED_MODEL nomic-embed-text-v1.5)"
LLM_GGUF="$(setting LLM_GGUF models/llm/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf)"
EMBED_GGUF="$(setting EMBED_GGUF models/embed/nomic-embed-text-v1.5.Q8_0.gguf)"
WHISPER_MODEL="$(setting WHISPER_MODEL small.en)"
port_of() { printf '%s' "$1" | sed -E 's#^[a-z]+://[^:/]+:([0-9]+).*#\1#'; }
STT_URL="$(setting STT_URL http://localhost:8178)"
TTS_URL="$(setting TTS_URL http://localhost:8880)"
LIVEKIT_HTTP="$(setting LIVEKIT_URL ws://localhost:7880 | sed -e 's#^ws://#http://#' -e 's#^wss://#https://#')"

# id | required|optional | what is checked | fix hint
CHECKS=(
  "uv|required|uv on PATH|brew install uv"
  "docker|required|docker CLI on PATH|brew install --cask docker   (or: brew install colima docker docker-compose)"
  "docker-daemon|required|docker daemon running|open -a Docker   (or: colima start)"
  "compose|required|docker compose v2|update Docker Desktop   (or: brew install docker-compose)"
  "postgres-port|required|port $POSTGRES_PORT free or used by this project's Postgres|set POSTGRES_PORT=5433 in .env (port taken by another process), then make up"
  "llama-server|optional|llama.cpp llama-server on PATH|brew install llama.cpp"
  "llm|optional|LLM server on $LLM_URL serving $LLM_MODEL|llama-server -m $LLM_GGUF --alias $LLM_MODEL --jinja -c 8192 -ngl 99 --host 127.0.0.1 --port $(port_of "$LLM_URL")"
  "embed|optional|embeddings server on $EMBED_URL|llama-server -m $EMBED_GGUF --alias $EMBED_MODEL --embedding --pooling mean -c 2048 -ngl 99 --host 127.0.0.1 --port $(port_of "$EMBED_URL")"
  "whisper|optional|whisper.cpp server on $STT_URL|brew install whisper-cpp && whisper-server --host 127.0.0.1 --port 8178 -m models/whisper/ggml-$WHISPER_MODEL.bin --inference-path /v1/audio/transcriptions"
  "kokoro|optional|Kokoro-FastAPI on $TTS_URL|git clone https://github.com/remsky/Kokoro-FastAPI && cd Kokoro-FastAPI && ./start-gpu_mac.sh"
  "livekit|optional|livekit-server on $LIVEKIT_HTTP|brew install livekit && livekit-server --dev"
)

have() { command -v "$1" >/dev/null 2>&1; }
http_ok() { have curl && curl -fsS --max-time 2 -o /dev/null "$1" 2>/dev/null; }
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
models_json() { have curl && curl -fsS --max-time 2 "$1/models" 2>/dev/null; }

# Probe one check; print a detail line on stdout; return 0 if it passes.
unreachable() { echo "not reachable on $1"; return 1; }
probe() {
  case "$1" in
    uv) have uv || { echo "not found on PATH"; return 1; }; uv --version 2>/dev/null | head -n 1 ;;
    docker) have docker || { echo "not found on PATH"; return 1; }; docker --version 2>/dev/null | head -n 1 ;;
    docker-daemon)
      have docker && docker info --format 'server {{.ServerVersion}}' 2>/dev/null \
        || { echo "daemon not running"; return 1; } ;;
    compose) have docker && docker compose version --short 2>/dev/null || { echo "not available"; return 1; } ;;
    postgres-port)
      if have docker && [ -n "$(cd "$ROOT" && docker compose ps --status running -q postgres 2>/dev/null)" ]; then
        echo "this project's Postgres is running on :$POSTGRES_PORT"
      elif port_open "$POSTGRES_PORT"; then
        echo "port $POSTGRES_PORT is used by another process"; return 1
      else
        echo "port $POSTGRES_PORT is free (start Postgres with: make up)"
      fi ;;
    llama-server) have llama-server || { echo "not found on PATH"; return 1; }; echo "found" ;;
    llm)
      json="$(models_json "$LLM_URL")" || { unreachable "$LLM_URL"; return 1; }
      printf '%s' "$json" | grep -q "\"$LLM_MODEL\"" && echo "serving $LLM_MODEL on $LLM_URL" \
        || { echo "reachable but not serving $LLM_MODEL (start it with --alias $LLM_MODEL)"; return 1; } ;;
    embed) models_json "$EMBED_URL" >/dev/null && echo "reachable on $EMBED_URL" || unreachable "$EMBED_URL" ;;
    whisper) http_ok "$STT_URL/" && echo "reachable on $STT_URL" || unreachable "$STT_URL" ;;
    kokoro) http_ok "$TTS_URL/v1/models" && echo "reachable on $TTS_URL" || unreachable "$TTS_URL" ;;
    livekit) http_ok "$LIVEKIT_HTTP/" && echo "reachable on $LIVEKIT_HTTP" || unreachable "$LIVEKIT_HTTP" ;;
    *) return 1 ;;
  esac
}

failed_required=0
failed_optional=0
for entry in "${CHECKS[@]}"; do
  IFS='|' read -r id kind what hint <<EOF
$entry
EOF
  if [ "$DRY" = 1 ]; then
    printf '  %-24s %-9s %s\n' "$id" "$kind" "$what"
    continue
  fi
  if detail="$(probe "$id")"; then
    printf '✓ %s: %s\n' "$id" "${detail:-ok}"
  else
    suffix=""
    if [ "$kind" = optional ]; then
      suffix=" (optional until M2)"
      failed_optional=$((failed_optional + 1))
    else
      failed_required=$((failed_required + 1))
    fi
    printf '✗ %s: %s%s → %s\n' "$id" "${detail:-not found: $what}" "$suffix" "$hint"
  fi
done

[ "$DRY" = 1 ] && exit 0
echo
if [ "$failed_required" -gt 0 ] || { [ "$STRICT" = 1 ] && [ "$failed_optional" -gt 0 ]; }; then
  echo "doctor: $failed_required required and $failed_optional optional check(s) failed — see docs/dev-setup.md"
  exit 1
fi
echo "doctor: all required checks passed ($failed_optional optional missing — needed from M2; use --strict then)"
