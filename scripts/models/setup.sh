#!/usr/bin/env bash
# make models-setup — install the native (Metal) model servers and their model files (VOICE-R1).
#
#   scripts/models/setup.sh                    brew deps, missing model files, Kokoro-FastAPI
#   scripts/models/setup.sh --with-fallbacks   also Llama 3.2 3B (low-memory LLM fallback)
#   scripts/models/setup.sh --with-large-stt   also whisper large-v3-turbo
#   scripts/models/setup.sh --dry-run          print what would be installed/downloaded, change nothing
#
# Idempotent: installed brew formulae are skipped; a GGUF is downloaded only when the file at
# LLM_GGUF / EMBED_GGUF is missing (an existing GGUF, or one in the Hugging Face cache, is reused);
# whisper files are skipped when their sha256 matches. A second run downloads nothing.
set -euo pipefail
. "$(dirname "$0")/_env.sh"

DRY=0
FALLBACKS=0
LARGE_STT=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --with-fallbacks) FALLBACKS=1 ;;
    --with-large-stt) LARGE_STT=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

HF="https://huggingface.co"
KOKORO_REPO="https://github.com/remsky/Kokoro-FastAPI.git"
KOKORO_REF="$(setting KOKORO_REF v0.9.2)"
BREW_DEPS="llama.cpp whisper-cpp livekit livekit-cli"

# name | destination | Hugging Face repo | file | sha256 | bytes
LLM_ROW="llm|$LLM_GGUF|bartowski/Meta-Llama-3.1-8B-Instruct-GGUF|Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf|7b064f5842bf9532c91456deda288a1b672397a54fa729aa665952863033557c|4920739232"
EMBED_ROW="embed|$EMBED_GGUF|nomic-ai/nomic-embed-text-v1.5-GGUF|nomic-embed-text-v1.5.Q8_0.gguf|3e24342164b3d94991ba9692fdc0dd08e3fd7362e0aacc396a9a5c54a544c3b7|146146432"
FALLBACK_ROW="llm-3b|$ROOT/models/llm/Llama-3.2-3B-Instruct-Q4_K_M.gguf|bartowski/Llama-3.2-3B-Instruct-GGUF|Llama-3.2-3B-Instruct-Q4_K_M.gguf|6c1a2b41161032677be168d354123594c0e6e67d2b9227c84f296ad037c728ff|2019377696"
WHISPER_SMALL_ROW="whisper-small.en|$ROOT/models/whisper/ggml-small.en.bin|ggerganov/whisper.cpp|ggml-small.en.bin|c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d|487614201"
WHISPER_LARGE_ROW="whisper-large-v3-turbo|$ROOT/models/whisper/ggml-large-v3-turbo.bin|ggerganov/whisper.cpp|ggml-large-v3-turbo.bin|1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69|1624555275"

ROWS="$LLM_ROW
$EMBED_ROW
$WHISPER_SMALL_ROW"
[ "$FALLBACKS" = 1 ] && ROWS="$ROWS
$FALLBACK_ROW"
[ "$LARGE_STT" = 1 ] && ROWS="$ROWS
$WHISPER_LARGE_ROW"

run() { if [ "$DRY" = 1 ]; then echo "  would run: $*"; else "$@"; fi; }
sha_of() { shasum -a 256 "$1" | cut -d' ' -f1; }
human() { awk -v b="$1" 'BEGIN { if (b >= 1e9) printf "%.1f GB", b/1e9; else printf "%.0f MB", b/1e6 }'; }

# The copy llama-server -hf / huggingface-cli left in the Hugging Face cache, if any.
hf_cached() {
  local repo="$1" file="$2" dir
  dir="${HF_HOME:-$HOME/.cache/huggingface}/hub/models--$(printf '%s' "$repo" | sed 's#/#--#g')/snapshots"
  [ -d "$dir" ] || return 1
  ls "$dir"/*/"$file" 2>/dev/null | head -n 1
}

echo "== brew packages"
for dep in $BREW_DEPS; do
  if brew list --versions "$dep" >/dev/null 2>&1; then
    echo "  ✓ $dep (installed)"
  else
    echo "  + $dep"
    run brew install "$dep"
  fi
done

echo "== model files"
TODO=""
TOTAL=0
while IFS='|' read -r name dest repo file sha bytes; do
  if [ -f "$dest" ]; then
    case "$name" in
      whisper-*)
        if [ "$(sha_of "$dest")" = "$sha" ]; then echo "  ✓ $name ($dest, checksum ok)"; continue; fi
        echo "  ! $name checksum mismatch, downloading again" ;;
      *) echo "  ✓ $name ($dest, reused)"; continue ;;
    esac
  fi
  if cached="$(hf_cached "$repo" "$file")" && [ -n "$cached" ]; then
    echo "  ↪ $name: linking the Hugging Face cache copy $cached"
    run mkdir -p "$(dirname "$dest")"
    run ln -sf "$cached" "$dest"
    continue
  fi
  TODO="$TODO$name|$dest|$repo|$file|$sha|$bytes
"
  TOTAL=$((TOTAL + bytes))
done <<EOF
$ROWS
EOF

KOKORO_NEW=0
if [ -d "$KOKORO_DIR/.git" ]; then
  echo "  ✓ kokoro-fastapi ($KOKORO_DIR)"
else
  KOKORO_NEW=1
fi

if [ -n "$TODO" ] || [ "$KOKORO_NEW" = 1 ]; then
  echo "== disk needed"
  printf '%s' "$TODO" | while IFS='|' read -r name dest repo file sha bytes; do
    echo "  $(human "$bytes")  $name → $dest"
  done
  [ "$KOKORO_NEW" = 1 ] && echo "  ~2 GB    Kokoro-FastAPI $KOKORO_REF (Python env with torch + the 82M model) → $KOKORO_DIR"
  echo "  total model downloads: $(human "$TOTAL"); free on this disk: $(df -h "$ROOT" | awk 'NR==2 {print $4}')"
else
  echo "== nothing to download"
fi

printf '%s' "$TODO" | while IFS='|' read -r name dest repo file sha bytes; do
  [ -n "$name" ] || continue
  echo "== downloading $name"
  run mkdir -p "$(dirname "$dest")"
  run curl -L --fail --retry 3 -C - -o "$dest.part" "$HF/$repo/resolve/main/$file"
  if [ "$DRY" = 0 ]; then
    if [ "$(sha_of "$dest.part")" != "$sha" ]; then
      echo "  ✗ $name: sha256 mismatch, kept $dest.part for inspection" >&2
      exit 1
    fi
    mv "$dest.part" "$dest"
    echo "  ✓ $name"
  fi
done

if [ "$KOKORO_NEW" = 1 ]; then
  echo "== Kokoro-FastAPI $KOKORO_REF"
  run mkdir -p "$ROOT/vendor"
  run git clone --depth 1 --branch "$KOKORO_REF" "$KOKORO_REPO" "$KOKORO_DIR"
fi
# The Python env and the voice model; start-gpu_mac.sh (run by up.sh) then finds them in place.
# A marker written only after both succeed makes an interrupted install (e.g. a network drop) resume.
KOKORO_DONE="$KOKORO_DIR/.supportagent-setup-done"
if [ "$KOKORO_NEW" = 1 ] || [ ! -f "$KOKORO_DONE" ]; then
  echo "== Kokoro-FastAPI Python env + voice model"
  # Kokoro gets its own environment (its Python is pinned in .python-version, 3.12). VIRTUAL_ENV is
  # cleared so an activated project venv never receives Kokoro's torch/spaCy (they clash with ours).
  run env -u VIRTUAL_ENV sh -c "cd '$KOKORO_DIR' && { [ -d .venv ] || uv venv --python \"\$(cat .python-version)\"; } && \
    uv pip install -e . && \
    uv run --no-sync python docker/scripts/download_model.py --output api/src/models/v1_0 && \
    touch '$KOKORO_DONE'"
else
  echo "  ✓ kokoro env + model (installed)"
fi

echo "done. Next: make models-up && make models-health"
