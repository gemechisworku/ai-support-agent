#!/usr/bin/env bash
# make models-up — start the native model servers in the background (VOICE-R1).
#
#   scripts/models/up.sh                 start llm, embed, whisper, kokoro, livekit
#   scripts/models/up.sh llm embed       start only the named servers
#
# Every server binds to 127.0.0.1 on the port of its URL in .env. Pids go to var/run/<name>.pid,
# logs to var/log/<name>.log. A server whose port already answers is left alone (and not stopped by
# down.sh), so servers you started by hand keep running.
set -euo pipefail
. "$(dirname "$0")/_env.sh"

ALL="llm embed whisper kokoro livekit"
WANT="${*:-$ALL}"
WAIT_S="${MODELS_UP_WAIT_S:-180}"
mkdir -p "$RUN_DIR" "$LOG_DIR"

LLM_PORT="$(port_of "$LLM_URL")"
EMBED_PORT="$(port_of "$EMBED_URL")"
STT_PORT="$(port_of "$STT_URL")"
TTS_PORT="$(port_of "$TTS_URL")"
LIVEKIT_PORT="$(port_of "$LIVEKIT_URL")"

port_open() { nc -z 127.0.0.1 "$1" >/dev/null 2>&1; }

need_file() {
  if [ ! -e "$1" ]; then
    echo "  ✗ $2: $1 is missing. Run: make models-setup" >&2
    return 1
  fi
}

start() {
  local name="$1" port="$2"
  shift 2
  local pidfile="$RUN_DIR/$name.pid" log="$LOG_DIR/$name.log"
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "  ✓ $name already running (pid $(cat "$pidfile"))"
    return 0
  fi
  if port_open "$port"; then
    echo "  ✓ $name: port $port already in use, left as is (not managed by up/down)"
    return 0
  fi
  echo "  + $name on 127.0.0.1:$port (log var/log/$name.log)"
  nohup "$@" >"$log" 2>&1 &
  echo $! >"$pidfile"
}

wait_for() {
  local name="$1" port="$2" waited=0
  [ -f "$RUN_DIR/$name.pid" ] || return 0
  while ! port_open "$port"; do
    if ! kill -0 "$(cat "$RUN_DIR/$name.pid")" 2>/dev/null; then
      echo "  ✗ $name exited; last log lines:" >&2
      tail -n 15 "$LOG_DIR/$name.log" >&2
      rm -f "$RUN_DIR/$name.pid"
      return 1
    fi
    if [ "$waited" -ge "$WAIT_S" ]; then
      echo "  ✗ $name not listening on $port after ${WAIT_S}s (see var/log/$name.log)" >&2
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
}

port_for() {
  case "$1" in
    llm) echo "$LLM_PORT" ;; embed) echo "$EMBED_PORT" ;; whisper) echo "$STT_PORT" ;;
    kokoro) echo "$TTS_PORT" ;; livekit) echo "$LIVEKIT_PORT" ;;
    *) echo "unknown server: $1 (choose from: $ALL)" >&2; exit 2 ;;
  esac
}

FAILED=0
for name in $WANT; do
  port="$(port_for "$name")"
  if [ ! -f "$RUN_DIR/$name.pid" ] && port_open "$port"; then
    echo "  ✓ $name: port $port already in use, left as is (not managed by up/down)"
    continue
  fi
  case "$name" in
    llm)
      need_file "$LLM_GGUF" llm || { FAILED=1; continue; }
      start llm "$LLM_PORT" llama-server -m "$LLM_GGUF" --alias "$LLM_MODEL" --jinja -c 8192 -ngl 99 \
        --host 127.0.0.1 --port "$LLM_PORT" ;;
    embed)
      need_file "$EMBED_GGUF" embed || { FAILED=1; continue; }
      start embed "$EMBED_PORT" llama-server -m "$EMBED_GGUF" --alias "$EMBED_MODEL" --embedding \
        --pooling mean -c 2048 -ngl 99 --host 127.0.0.1 --port "$EMBED_PORT" ;;
    whisper)
      need_file "$WHISPER_BIN" whisper || { FAILED=1; continue; }
      start whisper "$STT_PORT" whisper-server -m "$WHISPER_BIN" --host 127.0.0.1 --port "$STT_PORT" \
        --inference-path /v1/audio/transcriptions ;;
    kokoro)
      need_file "$KOKORO_DIR/start-gpu_mac.sh" kokoro || { FAILED=1; continue; }
      start kokoro "$TTS_PORT" env -u VIRTUAL_ENV HOST=127.0.0.1 PORT="$TTS_PORT" \
        sh -c "cd '$KOKORO_DIR' && exec ./start-gpu_mac.sh" ;;
    livekit)
      start livekit "$LIVEKIT_PORT" livekit-server --dev --bind 127.0.0.1 --config-body "port: $LIVEKIT_PORT" ;;
  esac
done

for name in $WANT; do
  wait_for "$name" "$(port_for "$name")" || FAILED=1
done

[ "$FAILED" = 0 ] && echo "up. Check: make models-health" || exit 1
