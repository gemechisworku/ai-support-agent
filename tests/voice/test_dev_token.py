"""T-VOICE-05 · VOICE-R7: scripts/dev_token.py mints a room token for the browser playground."""

import json
import sys
from pathlib import Path

import jwt
import pytest
from pydantic import SecretStr

from supportagent.common.settings import LiveKitSettings, Settings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import dev_token  # noqa: E402  (a script, not a package)

# `livekit-server --dev` uses the key `devkey` / secret `secret` (6 bytes), below JWT's recommended 32.
pytestmark = pytest.mark.filterwarnings("ignore:The HMAC key is")


def _with_livekit(settings: Settings, **fields: object) -> Settings:
    merged = LiveKitSettings(_env_file=None, **{**settings.livekit.model_dump(), **fields})  # type: ignore[arg-type]
    return settings.model_copy(update={"livekit": merged})


def _decode(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=["HS256"], options={"verify_aud": False})


def test_the_token_carries_the_room_identity_and_grants(settings: Settings) -> None:
    token = dev_token.mint_token(settings, room="playground-abc123", identity="dev-caller-1")
    claims = _decode(token, settings.livekit.livekit_api_secret.get_secret_value())

    assert claims["iss"] == settings.livekit.livekit_api_key
    assert claims["sub"] == "dev-caller-1"
    video = claims["video"]
    assert video["room"] == "playground-abc123" and video["roomJoin"] is True
    assert video["canPublish"] is True and video["canSubscribe"] is True


def test_the_key_and_secret_come_from_settings(settings: Settings) -> None:
    custom = _with_livekit(settings, livekit_api_key="otherkey", livekit_api_secret=SecretStr("othersecret"))
    token = dev_token.mint_token(custom, room="r", identity="i")

    claims = _decode(token, "othersecret")  # raises unless signed with the configured secret
    assert claims["iss"] == "otherkey"
    with pytest.raises(jwt.InvalidSignatureError):
        _decode(token, "secret")


def test_the_token_dispatches_the_support_agent_into_the_room(settings: Settings) -> None:
    """The worker registers with an explicit agent name, so the token itself must ask for it."""
    claims = _decode(
        dev_token.mint_token(settings, room="r", identity="i"),
        settings.livekit.livekit_api_secret.get_secret_value(),
    )
    assert [a["agentName"] for a in claims["roomConfig"]["agents"]] == ["support-agent"]


def test_the_dispatched_name_is_the_name_the_worker_registers() -> None:
    from supportagent.agent.worker import AGENT_NAME

    assert dev_token.AGENT_NAME == AGENT_NAME


def test_the_token_expires(settings: Settings) -> None:
    claims = _decode(
        dev_token.mint_token(settings, room="r", identity="i", ttl_s=600),
        settings.livekit.livekit_api_secret.get_secret_value(),
    )
    # LiveKit stamps nbf and exp in whole seconds, so across a second boundary the gap can read 601.
    assert 0 < claims["exp"] - claims["nbf"] <= 600 + 2


def test_default_names_are_fresh() -> None:
    assert dev_token.new_room_name() != dev_token.new_room_name()
    assert dev_token.new_room_name().startswith("playground-")
    assert dev_token.new_identity().startswith("dev-caller-")


def test_main_prints_the_token_but_never_the_secret(
    settings: Settings, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev_token, "get_settings", lambda: settings)
    assert dev_token.main(["--room", "my-room"]) == 0
    out = capsys.readouterr().out
    assert "my-room" in out and settings.livekit.livekit_url in out
    token = next(line.split()[-1] for line in out.splitlines() if line.startswith("token"))
    assert (
        _decode(token, settings.livekit.livekit_api_secret.get_secret_value())["video"]["room"] == "my-room"
    )
    assert settings.livekit.livekit_api_secret.get_secret_value() not in out.replace(token, "")


def test_main_json_is_machine_readable(
    settings: Settings, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev_token, "get_settings", lambda: settings)
    assert dev_token.main(["--json", "--room", "r1", "--identity", "me"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["room"], data["identity"], data["url"]) == ("r1", "me", settings.livekit.livekit_url)
    assert _decode(data["token"], settings.livekit.livekit_api_secret.get_secret_value())["sub"] == "me"
