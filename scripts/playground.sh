#!/usr/bin/env bash
# make playground — run LiveKit's open-source Agents Playground on this Mac (T-VOICE-05 · VOICE-R7).
#
#   scripts/playground.sh             set up (first run) and start it on http://127.0.0.1:3100
#   scripts/playground.sh --dry-run   print what it would do; clone, install and start nothing
#
# The hosted playground (agents-playground.livekit.io) is being retired by LiveKit, which keeps the open-source
# one "available for self-hosted use". This clones it into the git-ignored vendor/agents-playground at a pinned
# commit (PLAYGROUND_REF), writes its .env.local from LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET, runs
# `npm install` once, and starts it bound to 127.0.0.1. Open it, paste LIVEKIT_URL and the token that
# `make dev-token` prints, and connect. Needs Node and npm (see docs/dev-setup.md). Idempotent.
set -euo pipefail
. "$(dirname "$0")/models/_env.sh"

DRY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

REPO="https://github.com/livekit/agents-playground.git"
REF="$(setting PLAYGROUND_REF 2cfc4ac2257dabb4385f8a436e65cfd8a739d357)"
PORT="$(setting PLAYGROUND_PORT 3100)"
DIR="$(setting PLAYGROUND_DIR "$ROOT/vendor/agents-playground")"
DONE="$DIR/.supportagent-install-done"
API_KEY="$(setting LIVEKIT_API_KEY devkey)"
API_SECRET="$(setting LIVEKIT_API_SECRET secret)"
# The playground's browser code connects to this URL: the loopback address, never `localhost` (see dev-setup).
WS_URL="$(setting LIVEKIT_URL ws://127.0.0.1:7880)"

run() { if [ "$DRY" = 1 ]; then echo "  would run: $*"; else "$@"; fi; }

command -v npm >/dev/null 2>&1 || {
  [ "$DRY" = 1 ] || { echo "npm not found. Install Node (brew install node), then run this again." >&2; exit 1; }
}

echo "== Agents Playground source ($REF)"
if [ -d "$DIR/.git" ]; then
  echo "  ✓ $DIR"
else
  run mkdir -p "$ROOT/vendor"
  run git init -q "$DIR"
  run git -C "$DIR" remote add origin "$REPO"
  run git -C "$DIR" fetch -q --depth 1 origin "$REF"
  run git -C "$DIR" checkout -q FETCH_HEAD
fi

echo "== .env.local (LiveKit URL $WS_URL, key $API_KEY)"
if [ "$DRY" = 1 ]; then
  echo "  would write $DIR/.env.local from .env.example"
else
  awk -v k="$API_KEY" -v s="$API_SECRET" -v u="$WS_URL" '
    /^LIVEKIT_API_KEY=/ { print "LIVEKIT_API_KEY=" k; next }
    /^LIVEKIT_API_SECRET=/ { print "LIVEKIT_API_SECRET=" s; next }
    /^NEXT_PUBLIC_LIVEKIT_URL=/ { print "NEXT_PUBLIC_LIVEKIT_URL=" u; next }
    { print }' "$DIR/.env.example" >"$DIR/.env.local"
fi

echo "== npm packages"
if [ -f "$DONE" ]; then
  echo "  ✓ installed"
else
  # The marker is written only after a complete install, so an interrupted one (network drop) resumes.
  run sh -c "cd '$DIR' && npm install --no-audit --no-fund && touch '$DONE'"
fi

cat <<EOF

Playground: http://127.0.0.1:$PORT   (Ctrl-C stops it)
  1. In another terminal:  make dev-token
  2. In the page, paste  $WS_URL  and the token, then click Connect and allow the microphone.
EOF
echo
run sh -c "cd '$DIR' && exec npm run dev -- -p '$PORT' -H 127.0.0.1"
