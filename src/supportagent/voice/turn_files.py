"""The English end-of-turn model's files (VOICE-R4): download them once, and check they are really there.

`livekit-plugins-turn-detector` loads its weights with `local_files_only=True`, so a call that starts without
them dies in `EnglishModel()` and the agent leaves the room. `python -m supportagent.agent.worker
download-files` does NOT fetch them: it only fetches for plugins already imported, and ours imports this one
lazily (it exits 0 having fetched nothing). So this module downloads exactly the English model (not the
multilingual one) and `files_present` checks the Hugging Face cache itself, instead of trusting a marker file.

    uv run python -m supportagent.voice.turn_files     # download if missing, then verify; exit 1 on failure
"""

from __future__ import annotations

import sys
from pathlib import Path

# The files the English model needs at load time (see livekit.plugins.turn_detector.base): the ONNX weights,
# the language thresholds and the tokenizer.
FILES = ("onnx/model_q8.onnx", "languages.json", "tokenizer.json")


def _repo_and_revision() -> tuple[str, str]:
    from livekit.plugins.turn_detector.models import HG_MODEL, MODEL_REVISIONS

    return HG_MODEL, MODEL_REVISIONS["en"]


def missing_files(cache_dir: str | Path | None = None) -> list[str]:
    """The model files that are not in the Hugging Face cache (empty when the model is ready to load)."""
    from huggingface_hub import try_to_load_from_cache

    repo, revision = _repo_and_revision()
    missing = []
    for name in FILES:
        found = try_to_load_from_cache(repo, name, cache_dir=cache_dir, revision=revision)
        if not isinstance(found, str):  # None, or the "known not to exist" sentinel
            missing.append(name)
    return missing


def files_present(cache_dir: str | Path | None = None) -> bool:
    return not missing_files(cache_dir)


def download() -> None:
    """Fetch the English model's files (needs the network). Uses the plugin's own downloader."""
    from livekit.plugins.turn_detector.english import _EUORunnerEn

    _EUORunnerEn._download_files()


def main() -> int:
    if files_present():
        print("turn-detector files: already in the cache")
        return 0
    print("turn-detector files: downloading the English model (once, needs the network)...", flush=True)
    try:
        download()
    except Exception as exc:
        print(f"turn-detector files: download failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    left = missing_files()
    if left:
        print(f"turn-detector files: still missing after the download: {', '.join(left)}", file=sys.stderr)
        return 1
    print("turn-detector files: ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
