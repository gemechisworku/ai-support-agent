"""T-AGENT-04 · AGENT-R4 / COMP-R5 (first real call, 2026-11-17): the recording disclosure must
actually be heard.

On the real call the caller's first words, 0.9 s in, cancelled the disclosure before the TTS had produced any
audio: it was never spoken, the transcript recorder never saw it (LiveKit adds a spoken line to the
conversation
only once text was forwarded to the audio output), yet `disclosures_given` already listed it. So the
disclosure is
now uninterruptible, and its tag is recorded only when the speech finished without being interrupted.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from supportagent.agent.disclose import registry_for, speak_disclosure
from supportagent.agent.finalize import attach_recorder
from tests.agent.test_finalize import _state

pytestmark = pytest.mark.usefixtures("settings")  # the registry file is found through the settings


class FakeHandle:
    """The part of `SpeechHandle` that `speak_disclosure` uses."""

    def __init__(self) -> None:
        self.interrupted = False
        self._callbacks: list[Callable[[Any], None]] = []

    def add_done_callback(self, callback: Callable[[Any], None]) -> None:
        self._callbacks.append(callback)

    def finish(self, *, interrupted: bool = False) -> None:
        self.interrupted = interrupted
        for callback in self._callbacks:
            callback(self)


class FakeSession:
    def __init__(self) -> None:
        self.said: list[tuple[str, dict[str, Any]]] = []
        self.handles: list[FakeHandle] = []

    def say(self, text: str, **kwargs: Any) -> FakeHandle:
        self.said.append((text, kwargs))
        handle = FakeHandle()
        self.handles.append(handle)
        return handle


def _agent() -> Any:
    state = _state()
    state.states_visited = ["greeting"]
    return SimpleNamespace(
        state=state,
        session=FakeSession(),
        variables={"bank_name": "Meridian Bank", "agent_name": "Ava"},
        state_name="greeting",
    )


def test_the_disclosure_cannot_be_interrupted() -> None:
    agent = _agent()
    speak_disclosure(agent, "ai_recording")
    text, kwargs = agent.session.said[0]
    assert "Ava" in text and "Meridian Bank" in text
    assert kwargs == {"allow_interruptions": False}


def test_the_tag_is_recorded_only_once_the_disclosure_has_been_spoken() -> None:
    agent = _agent()
    speak_disclosure(agent, "ai_recording")
    assert agent.state.disclosures_given == []  # said, not yet heard
    agent.session.handles[0].finish()
    assert agent.state.disclosures_given == [registry_for(agent.state).tag("ai_recording")]


def test_an_interrupted_disclosure_is_not_recorded_as_given() -> None:
    agent = _agent()
    speak_disclosure(agent, "ai_recording")
    agent.session.handles[0].finish(interrupted=True)
    assert agent.state.disclosures_given == []


def test_speaking_the_same_disclosure_twice_records_it_once() -> None:
    agent = _agent()
    speak_disclosure(agent, "ai_recording")
    speak_disclosure(agent, "ai_recording")
    for handle in agent.session.handles:
        handle.finish()
    assert len(agent.state.disclosures_given) == 1


# ---------------------------------------------------------------- the transcript sees what was spoken


class RecorderSession:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def on(self, event: str, handler: Any = None) -> Any:
        self.handlers[event] = handler
        return handler

    def say_added(self, text: str) -> None:
        """LiveKit adds a spoken line as an assistant message once its text reached the audio output."""
        item = SimpleNamespace(type="message", role="assistant", text_content=text)
        self.handlers["conversation_item_added"](SimpleNamespace(item=item))


def test_a_spoken_disclosure_and_the_next_reply_are_recorded_in_order_exactly_once() -> None:
    agent = _agent()
    session = RecorderSession()
    attach_recorder(session, agent.state)  # type: ignore[arg-type]

    handle = speak_disclosure(agent, "ai_recording")
    disclosure_text = agent.session.said[0][0]
    session.say_added(disclosure_text)  # the disclosure was played
    handle.finish()
    agent.state.states_visited.append("router")
    session.say_added("How can I help?")  # the router's reply

    turns = [(t.speaker, t.state, t.text) for t in agent.state.outcome.turns if t.speaker != "system"]
    assert turns[0][:2] == ("agent", "greeting") and turns[0][2].startswith(disclosure_text[:20])
    assert turns[1] == ("agent", "router", "How can I help?")
    assert len(turns) == 2  # each spoken line once, in order
