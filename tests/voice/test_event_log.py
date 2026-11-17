"""T-VOICE-05 · VOICE-R4: the JSON-lines event log that feeds the live barge-in check."""

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from supportagent.voice.event_log import attach_event_log
from supportagent.voice.session import barge_in_latency_s


class FakeSession:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Callable[..., Any]]] = {}

    def on(self, event: str, callback: Callable[..., Any]) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def emit(self, event: str, **fields: Any) -> None:
        for handler in self.handlers.get(event, []):
            handler(SimpleNamespace(**fields))


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_state_changes_are_appended_as_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "events.jsonl"  # the folder is created on demand
    session = FakeSession()
    attach_event_log(session, path)

    session.emit("agent_state_changed", new_state="speaking", created_at=100.0)
    session.emit("user_state_changed", new_state="speaking", created_at=100.5)
    session.emit("agent_state_changed", new_state="listening", created_at=100.62)

    assert _lines(path) == [
        {"type": "agent_state_changed", "new_state": "speaking", "created_at": 100.0},
        {"type": "user_state_changed", "new_state": "speaking", "created_at": 100.5},
        {"type": "agent_state_changed", "new_state": "listening", "created_at": 100.62},
    ]


def test_a_missing_timestamp_falls_back_to_the_clock(tmp_path: Path) -> None:
    path = tmp_path / "e.jsonl"
    session = FakeSession()
    attach_event_log(session, path, clock=lambda: 42.0)
    session.emit("agent_state_changed", new_state="thinking")
    assert _lines(path)[0]["created_at"] == 42.0


def test_only_states_and_times_are_written(tmp_path: Path) -> None:
    path = tmp_path / "e.jsonl"
    session = FakeSession()
    attach_event_log(session, path)
    session.emit("user_state_changed", new_state="speaking", created_at=1.0, transcript="my name is Frank")
    assert set(_lines(path)[0]) == {"type", "new_state", "created_at"}
    assert "Frank" not in path.read_text()


def test_the_log_is_what_the_barge_in_measurement_reads(tmp_path: Path) -> None:
    path = tmp_path / "e.jsonl"
    session = FakeSession()
    attach_event_log(session, path)
    session.emit("agent_state_changed", new_state="speaking", created_at=10.0)
    session.emit(
        "user_state_changed", new_state="speaking", created_at=11.0
    )  # the caller talks over the agent
    session.emit("agent_state_changed", new_state="listening", created_at=11.2)

    assert barge_in_latency_s(_lines(path)) == pytest.approx(0.2)
