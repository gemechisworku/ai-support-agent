"""Live checks against the real native model servers (`make models-up`). Marker `live`: never in CI."""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from livekit import rtc

from supportagent.common.settings import load_settings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import agent_dev  # noqa: E402  (scripts, not packages)
import dev_token  # noqa: E402

pytestmark = pytest.mark.live

GREETING_BUDGET_S = 5.0
SERVERS = ("llm", "whisper", "kokoro", "livekit")


def test_servers_healthy() -> None:
    """T-VOICE-01 · VOICE-R1: every native server answers health.py."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "models" / "health.py")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def test_playground_greeting() -> None:
    """T-VOICE-05 · VOICE-R7: joining a fresh room with a minted token brings the agent in, and it speaks.

    Needs the stack from the "Talk to the agent" guide: `make models-up`, the gateway
    (`docker compose up -d --wait postgres bank-api gateway`) and `make agent-dev` running in another
    terminal. A headless participant joins and the test passes when the agent publishes an audio track
    within 5 s (the recording disclosure is the first thing it says). Run it twice if the first run follows
    a cold start of the worker: loading the VAD and the first TTS request take a few seconds. It skips
    when a server is down.
    """
    settings = load_settings()
    health = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "models" / "health.py"), *SERVERS],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if health.returncode != 0:
        pytest.skip("a model server is not up: run `make models-up`\n" + health.stdout)
    if not agent_dev.gateway_up(settings):
        pytest.skip(
            "the gateway is not reachable: run `docker compose up -d --wait postgres bank-api gateway`"
        )

    token = dev_token.mint_token(
        settings, room=dev_token.new_room_name(), identity=dev_token.new_identity(), ttl_s=300
    )
    room = rtc.Room()
    agent_audio = asyncio.Event()

    @room.on("track_subscribed")
    def _on_track(
        track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant
    ) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            agent_audio.set()

    await room.connect(settings.livekit.livekit_url, token)
    try:
        await asyncio.wait_for(agent_audio.wait(), GREETING_BUDGET_S)
    except TimeoutError:
        pytest.fail(
            f"the agent published no audio within {GREETING_BUDGET_S:.0f} s: is `make agent-dev` running "
            "(see var/log or its terminal for errors)?"
        )
    finally:
        await room.disconnect()
