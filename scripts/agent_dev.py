#!/usr/bin/env python3
"""make agent-dev — run the agent worker in dev mode against the local LiveKit server (T-VOICE-05 · VOICE-R7).

    make agent-dev

Before it starts the worker it checks what the agent needs and fails with a one-line hint when something is
missing:
- the native model servers (LLM, whisper, Kokoro, LiveKit): `make models-up`;
- the gateway: `docker compose up -d --wait postgres bank-api gateway` (it serves the agent's tools);
- the turn-detector weights, fetched once with `python -m supportagent.voice.turn_files` and checked in the
  Hugging Face cache on every start (so later runs do not need the network).

LiveKit's worker CLI reads LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET from the process environment, so
this script takes them from `supportagent.common.settings` (the project's `.env`) and passes them on: nothing
has to be exported in the shell. `SESSION_EVENT_LOG` is set to `var/log/session_events.jsonl` for the live
barge-in check unless it is already configured.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supportagent.common.settings import Settings, get_settings  # noqa: E402

WORKER = "supportagent.agent.worker"
TURN_FILES = "supportagent.voice.turn_files"
DEFAULT_EVENT_LOG = "var/log/session_events.jsonl"
SERVERS = ("llm", "whisper", "kokoro", "livekit")


def worker_env(settings: Settings, base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for the worker: LiveKit connection from settings, plus the dev event log."""
    lk = settings.livekit
    env = dict(os.environ if base is None else base)
    env["LIVEKIT_URL"] = lk.livekit_url
    env["LIVEKIT_API_KEY"] = lk.livekit_api_key
    env["LIVEKIT_API_SECRET"] = lk.livekit_api_secret.get_secret_value()
    if not settings.models.session_event_log:
        env["SESSION_EVENT_LOG"] = DEFAULT_EVENT_LOG
    return env


def gateway_base_url(settings: Settings) -> str:
    """`http://localhost:8002/mcp` → `http://localhost:8002`."""
    parts = urlsplit(settings.services.gateway_url)
    return f"{parts.scheme}://{parts.netloc}"


def gateway_up(settings: Settings, *, timeout_s: float = 3.0) -> bool:
    try:
        return httpx.get(f"{gateway_base_url(settings)}/healthz", timeout=timeout_s).status_code == 200
    except httpx.HTTPError:
        return False


def turn_detector_ready() -> bool:
    """Are the end-of-turn model's files really in the Hugging Face cache? (Not a marker: see turn_files.)"""
    from supportagent.voice import turn_files

    return turn_files.files_present()


def fail(message: str) -> int:
    print(f"agent-dev: {message}", file=sys.stderr)
    return 1


def main() -> int:
    settings = get_settings()

    health = subprocess.run(  # noqa: S603  (our own interpreter and script, fixed arguments)
        [sys.executable, str(ROOT / "scripts" / "models" / "health.py"), *SERVERS], cwd=ROOT, check=False
    )
    if health.returncode != 0:
        return fail("a model server is not healthy (see above): start them with `make models-up`")
    if not gateway_up(settings):
        return fail(
            f"the gateway is not reachable at {gateway_base_url(settings)}: "
            "run `docker compose up -d --wait postgres bank-api gateway`"
        )

    env = worker_env(settings)
    if not turn_detector_ready():
        print("agent-dev: downloading the turn-detector weights (once)...", flush=True)
        download = subprocess.run(  # noqa: S603  (our own interpreter, fixed arguments)
            [sys.executable, "-W", "ignore", "-m", TURN_FILES], cwd=ROOT, env=env, check=False
        )
        if download.returncode != 0 or not turn_detector_ready():
            return fail(f"`python -m {TURN_FILES}` failed (needs the network once): see above")

    print("agent-dev: starting the worker. In another terminal run `make dev-token` and open the playground.")
    sys.stdout.flush()
    os.chdir(ROOT)
    os.execvpe(sys.executable, [sys.executable, "-m", WORKER, "dev"], env)  # noqa: S606  (replaces this launcher)
    return 0  # not reached


if __name__ == "__main__":
    sys.exit(main())
