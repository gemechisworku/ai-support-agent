#!/usr/bin/env python3
"""make models-health — probe the native model servers (VOICE-R1). Standard library only.

    python3 scripts/models/health.py                 all five servers
    python3 scripts/models/health.py llm embed       only these

Exit 0 when every probed server is healthy, else 1; each failure names the server and the reason.
URLs and model aliases come from the environment, else `.env`, else the `.env.example` defaults
(like scripts/doctor.sh), so this runs before `uv sync` and without the app's settings package.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_S = float(os.environ.get("MODELS_HEALTH_TIMEOUT_S", "3"))
DEFAULTS = {
    "LLM_URL": "http://127.0.0.1:8090/v1",
    "EMBED_URL": "http://127.0.0.1:8082/v1",
    "LLM_MODEL": "llama-3.1-8b-instruct",
    "EMBED_MODEL": "nomic-embed-text-v1.5",
    "STT_URL": "http://127.0.0.1:8178",
    "TTS_URL": "http://127.0.0.1:8880",
    "LIVEKIT_URL": "ws://127.0.0.1:7880",
}


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
    return values


def setting(key: str, env_file: dict[str, str]) -> str:
    return os.environ.get(key) or env_file.get(key) or DEFAULTS[key]


class Unhealthy(Exception):
    pass


def get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:  # noqa: S310 - local URLs from .env
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise Unhealthy(f"{url} unreachable ({reason})") from exc


def expect_ok(url: str) -> bytes:
    status, body = get(url)
    if status != 200:
        raise Unhealthy(f"{url} returned HTTP {status}")
    return body


def model_ids(body: bytes, url: str) -> list[str]:
    try:
        data = json.loads(body)
        return [str(m.get("id")) for m in data.get("data", [])] + [
            str(m.get("name") or m.get("model")) for m in data.get("models", [])
        ]
    except (ValueError, AttributeError) as exc:
        raise Unhealthy(f"{url} did not return a model list") from exc


def root_of(url: str) -> str:
    """http://host:8090/v1 → http://host:8090 (llama-server's /health is not under /v1)."""
    url = url.rstrip("/")
    return url[: -len("/v1")] if url.endswith("/v1") else url


def llama(base: str, model: str) -> Callable[[], str]:
    def probe() -> str:
        url = f"{base.rstrip('/')}/models"
        ids = model_ids(expect_ok(url), url)
        if model not in ids:
            raise Unhealthy(f"model {model!r} not served (got {', '.join(ids) or 'none'})")
        expect_ok(f"{root_of(base)}/health")
        return f"serves {model}"

    return probe


def plain(url: str) -> Callable[[], str]:
    def probe() -> str:
        expect_ok(url)
        return "ok"

    return probe


def probes(env_file: dict[str, str]) -> dict[str, tuple[str, Callable[[], str]]]:
    s = lambda key: setting(key, env_file)  # noqa: E731
    livekit = s("LIVEKIT_URL").replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
    return {
        "llm": (s("LLM_URL"), llama(s("LLM_URL"), s("LLM_MODEL"))),
        "embed": (s("EMBED_URL"), llama(s("EMBED_URL"), s("EMBED_MODEL"))),
        "whisper": (s("STT_URL"), plain(f"{s('STT_URL').rstrip('/')}/")),
        "kokoro": (s("TTS_URL"), plain(f"{s('TTS_URL').rstrip('/')}/v1/models")),
        "livekit": (livekit, plain(f"{livekit}/")),
    }


def main(argv: list[str]) -> int:
    env_file = read_env_file(Path(os.environ.get("MODELS_ENV_FILE", ROOT / ".env")))
    table = probes(env_file)
    names = argv or list(table)
    unknown = [n for n in names if n not in table]
    if unknown:
        print(f"unknown server(s): {', '.join(unknown)} (choose from: {', '.join(table)})", file=sys.stderr)
        return 2
    failed = []
    for name in names:
        url, probe = table[name]
        try:
            print(f"  ✓ {name:<8} {url}  {probe()}")
        except Unhealthy as exc:
            failed.append(name)
            print(f"  ✗ {name:<8} {url}  {exc}")
    healthy = len(names) - len(failed)
    print(f"{healthy}/{len(names)} healthy" + (f"; failing: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
