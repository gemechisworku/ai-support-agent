"""T-VOICE-05 · VOICE-R7: scripts/agent_dev.py prepares the worker's environment from settings (offline)."""

import sys
from pathlib import Path

import httpx
import pytest

from supportagent.common.settings import ModelsSettings, ServicesSettings, Settings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import agent_dev  # noqa: E402  (a script, not a package)


def test_the_worker_gets_the_livekit_connection_from_settings(settings: Settings) -> None:
    env = agent_dev.worker_env(settings, base={"PATH": "/usr/bin"})
    assert env["LIVEKIT_URL"] == settings.livekit.livekit_url
    assert env["LIVEKIT_API_KEY"] == settings.livekit.livekit_api_key
    assert env["LIVEKIT_API_SECRET"] == settings.livekit.livekit_api_secret.get_secret_value()
    assert env["PATH"] == "/usr/bin"  # the rest of the environment is kept


def test_the_event_log_defaults_on_for_the_dev_worker_but_respects_a_configured_one(
    settings: Settings,
) -> None:
    assert agent_dev.worker_env(settings, base={})["SESSION_EVENT_LOG"] == agent_dev.DEFAULT_EVENT_LOG

    models = ModelsSettings(
        _env_file=None, **{**settings.models.model_dump(), "session_event_log": "var/log/mine.jsonl"}
    )  # type: ignore[arg-type]
    configured = settings.model_copy(update={"models": models})
    assert "SESSION_EVENT_LOG" not in agent_dev.worker_env(configured, base={})  # the setting itself is used


def test_gateway_base_url_drops_the_mcp_path(settings: Settings) -> None:
    services = ServicesSettings(
        _env_file=None, **{**settings.services.model_dump(), "gateway_url": "http://127.0.0.1:8002/mcp"}
    )  # type: ignore[arg-type]
    moved = settings.model_copy(update={"services": services})
    assert agent_dev.gateway_base_url(moved) == "http://127.0.0.1:8002"


def test_gateway_up_reflects_the_health_endpoint(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_dev.httpx, "get", lambda url, timeout: httpx.Response(200))
    assert agent_dev.gateway_up(settings) is True

    def refuse(url: str, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(agent_dev.httpx, "get", refuse)
    assert agent_dev.gateway_up(settings) is False


def test_the_turn_detector_download_is_remembered(tmp_path: Path) -> None:
    marker = tmp_path / "run" / "turn-detector.ok"
    assert agent_dev.turn_detector_ready(marker) is False
    marker.parent.mkdir()
    marker.touch()
    assert agent_dev.turn_detector_ready(marker) is True
