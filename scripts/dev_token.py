#!/usr/bin/env python3
"""make dev-token — mint a LiveKit room token for the browser playground (T-VOICE-05 · VOICE-R7).

    uv run python scripts/dev_token.py                    # a fresh room and identity
    uv run python scripts/dev_token.py --room my-room     # a named room
    uv run python scripts/dev_token.py --json             # machine-readable, for scripts

The key, secret and server URL come from `supportagent.common.settings` (LIVEKIT_API_KEY, LIVEKIT_API_SECRET,
LIVEKIT_URL: the dev defaults of `livekit-server --dev`). The token lets one participant join one room and
asks LiveKit to dispatch the `support-agent` worker into it: the worker registers with an explicit agent
name, so without that dispatch the room would stay empty. The room carries no call metadata, which the worker
treats as an inbound playground call (`campaign="playground"`).

Open https://agents-playground.livekit.io, choose a custom/manual connection, paste the URL and the token,
and connect. Only the token is printed (never the secret).
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import warnings
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from livekit import api  # noqa: E402

from supportagent.common.settings import Settings, get_settings  # noqa: E402

# The worker's registered name (`supportagent.agent.worker.AGENT_NAME`; a test keeps the two equal). It is a
# constant here because importing the worker would load the whole agent just to mint a token.
AGENT_NAME = "support-agent"
PLAYGROUND_URL = "https://agents-playground.livekit.io"
DEFAULT_TTL_S = 3600

# The documented dev key of `livekit-server --dev` is `devkey` / `secret` (6 bytes): PyJWT's length warning
# is noise for a local development token.
warnings.filterwarnings("ignore", message="The HMAC key is")


def new_room_name() -> str:
    return f"playground-{secrets.token_hex(3)}"


def new_identity() -> str:
    return f"dev-caller-{secrets.token_hex(3)}"


def mint_token(
    settings: Settings,
    *,
    room: str,
    identity: str,
    ttl_s: int = DEFAULT_TTL_S,
    agent_name: str = AGENT_NAME,
) -> str:
    """A JWT that joins `room` as `identity` and dispatches the agent worker `agent_name` into it."""
    lk = settings.livekit
    token = (
        api.AccessToken(lk.livekit_api_key, lk.livekit_api_secret.get_secret_value())
        .with_identity(identity)
        .with_name(identity)
        .with_ttl(timedelta(seconds=ttl_s))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=agent_name)]))
    )
    return str(token.to_jwt())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mint a LiveKit room token for the browser playground.")
    parser.add_argument("--room", default=None, help="room name (default: a fresh playground-xxxxxx)")
    parser.add_argument("--identity", default=None, help="participant identity (default: dev-caller-xxxxxx)")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL_S, help="token lifetime in seconds")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of the guide")
    args = parser.parse_args(argv)

    settings = get_settings()
    room = args.room or new_room_name()
    identity = args.identity or new_identity()
    token = mint_token(settings, room=room, identity=identity, ttl_s=args.ttl)
    url = settings.livekit.livekit_url

    if args.json:
        print(json.dumps({"url": url, "room": room, "identity": identity, "token": token}))
        return 0
    print(f"room      {room}")
    print(f"identity  {identity}")
    print(f"url       {url}")
    print(f"token     {token}")
    print()
    print(f"Open {PLAYGROUND_URL}, pick the manual connection, paste the url and token above, and connect.")
    print(f"The token is valid for {args.ttl // 60} minutes. Make sure `make agent-dev` is running first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
