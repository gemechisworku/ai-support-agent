#!/usr/bin/env bash
# make models-down — stop the servers that scripts/models/up.sh started (pids in var/run/).
# Servers started by hand are not touched.
set -uo pipefail
. "$(dirname "$0")/_env.sh"

# Kill a process and its descendants (Kokoro's start script runs uv → uvicorn as children).
kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$child"; done
  kill "$pid" 2>/dev/null || true
}

shopt -s nullglob
found=0
for pidfile in "$RUN_DIR"/*.pid; do
  found=1
  name="$(basename "$pidfile" .pid)"
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    kill_tree "$pid"
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    echo "  - $name stopped (pid $pid)"
  else
    echo "  - $name was not running"
  fi
  rm -f "$pidfile"
done
[ "$found" = 1 ] || echo "nothing to stop (no pid files in var/run/)"
