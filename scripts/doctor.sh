#!/usr/bin/env bash
# make doctor — check the local prerequisites (PLAT-R7). See docs/dev-setup.md.
#
#   scripts/doctor.sh            run all checks; exit 1 if a REQUIRED check fails
#   scripts/doctor.sh --strict   also fail on optional checks (use from milestone M2 on)
#   scripts/doctor.sh --dry-run  list the checks without probing anything
#
# Required: uv, Docker (CLI + running daemon), Docker Compose, a usable Postgres port.
# Optional until M2: the native Metal model servers (Ollama + models, whisper.cpp, Kokoro, LiveKit).
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
OLLAMA_URL="$(setting OLLAMA_URL http://localhost:11434)"
LLM_MODEL="$(setting LLM_MODEL qwen3:8b)"
EMBED_MODEL="$(setting EMBED_MODEL nomic-embed-text)"
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
  "ollama|optional|Ollama on $OLLAMA_URL|brew install ollama && brew services start ollama"
  "model:$LLM_MODEL|optional|Ollama model $LLM_MODEL pulled|ollama pull $LLM_MODEL"
  "model:$EMBED_MODEL|optional|Ollama model $EMBED_MODEL pulled|ollama pull $EMBED_MODEL"
  "whisper|optional|whisper.cpp server on $STT_URL|brew install whisper-cpp && whisper-server --host 127.0.0.1 --port 8178 -m models/ggml-large-v3-turbo.bin"
  "kokoro|optional|Kokoro-FastAPI on $TTS_URL|git clone https://github.com/remsky/Kokoro-FastAPI && cd Kokoro-FastAPI && ./start-gpu_mac.sh"
  "livekit|optional|livekit-server on $LIVEKIT_HTTP|brew install livekit && livekit-server --dev"
)

have() { command -v "$1" >/dev/null 2>&1; }
http_ok() { have curl && curl -fsS --max-time 2 -o /dev/null "$1" 2>/dev/null; }
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
OLLAMA_TAGS=""
ollama_tags() {
  if [ -z "$OLLAMA_TAGS" ] && have curl; then
    OLLAMA_TAGS="$(curl -fsS --max-time 2 "$OLLAMA_URL/api/tags" 2>/dev/null || true)"
  fi
  printf '%s' "$OLLAMA_TAGS"
}

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
    ollama) [ -n "$(ollama_tags)" ] && echo "reachable on $OLLAMA_URL" || unreachable "$OLLAMA_URL" ;;
    model:*)
      ollama_tags | grep -q "\"name\":\"${1#model:}" && echo "pulled" \
        || { echo "not pulled (or Ollama not running)"; return 1; } ;;
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
