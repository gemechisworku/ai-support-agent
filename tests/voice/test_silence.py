"""T-VOICE-03 · VOICE-R5: the silence policy with a fake clock (no sleeping, no LiveKit session)."""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from supportagent.voice.silence import SilenceAction, SilencePolicy, attach_silence_policy


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def policy(clock: FakeClock, **kw: Any) -> SilencePolicy:
    return SilencePolicy(reprompt_after_s=6, end_after_s=15, clock=clock, **kw)


def agent_finishes_talking(p: SilencePolicy) -> None:
    p.on_agent_state("speaking")
    p.on_agent_state("listening")


def test_nothing_happens_before_the_agent_has_spoken(clock: FakeClock) -> None:
    p = policy(clock)
    clock.advance(60)
    assert p.poll() is None


def test_reprompt_fires_once_after_the_threshold(clock: FakeClock) -> None:
    p = policy(clock)
    agent_finishes_talking(p)
    clock.advance(5.9)
    assert p.poll() is None
    clock.advance(0.1)
    assert p.poll() is SilenceAction.REPROMPT
    assert p.poll() is None  # not again, even though still silent
    clock.advance(5)
    assert p.poll() is None


def test_end_fires_at_the_end_threshold_measured_from_the_start_of_silence(clock: FakeClock) -> None:
    p = policy(clock)
    agent_finishes_talking(p)
    clock.advance(6)
    assert p.poll() is SilenceAction.REPROMPT
    agent_finishes_talking(p)  # the agent spoke the re-prompt: that does not restart the silence clock
    clock.advance(8.9)
    assert p.poll() is None
    clock.advance(0.1)  # 15.0 s of caller silence in total
    assert p.poll() is SilenceAction.END
    assert p.poll() is None  # the end is emitted once
    clock.advance(100)
    assert p.poll() is None


def test_end_without_a_reprompt_when_polled_late(clock: FakeClock) -> None:
    p = policy(clock)
    agent_finishes_talking(p)
    clock.advance(20)
    assert p.poll() is SilenceAction.END


def test_caller_speech_resets_the_timers_and_the_reprompt_budget(clock: FakeClock) -> None:
    p = policy(clock)
    agent_finishes_talking(p)
    clock.advance(6)
    assert p.poll() is SilenceAction.REPROMPT
    p.on_user_state("speaking")
    clock.advance(30)  # the caller talks for a long time: never silent
    assert p.poll() is None
    p.on_user_state("listening")
    agent_finishes_talking(p)  # the agent answers
    clock.advance(5.9)
    assert p.poll() is None
    clock.advance(0.1)
    assert p.poll() is SilenceAction.REPROMPT  # the budget was restored


def test_silence_after_a_reply_is_counted_from_the_end_of_the_reply(clock: FakeClock) -> None:
    p = policy(clock)
    agent_finishes_talking(p)
    p.on_user_state("speaking")
    clock.advance(4)
    p.on_user_state("listening")  # the caller stops; the clock starts...
    clock.advance(1)
    p.on_agent_state("thinking")  # ...but the agent replies after a second
    clock.advance(2)
    p.on_agent_state("speaking")
    clock.advance(10)  # a long answer
    assert p.poll() is None
    p.on_agent_state("listening")
    clock.advance(5.9)
    assert p.poll() is None  # only the quiet after the reply counts
    clock.advance(0.1)
    assert p.poll() is SilenceAction.REPROMPT


def test_no_reprompt_while_the_agent_is_speaking_or_thinking(clock: FakeClock) -> None:
    p = policy(clock)
    agent_finishes_talking(p)
    clock.advance(5)
    p.on_agent_state("speaking")  # e.g. a long answer
    clock.advance(60)
    assert p.poll() is None
    p.on_agent_state("thinking")
    clock.advance(60)
    assert p.poll() is None


def test_agent_speech_does_not_count_as_caller_activity(clock: FakeClock) -> None:
    p = policy(clock)
    agent_finishes_talking(p)
    clock.advance(4)
    agent_finishes_talking(p)  # an agent turn in the middle of the silence
    clock.advance(2)  # 6 s since the caller was last active
    assert p.poll() is SilenceAction.REPROMPT


def test_user_away_counts_as_silence(clock: FakeClock) -> None:
    p = policy(clock)
    agent_finishes_talking(p)
    p.on_user_state("away")
    clock.advance(6)
    assert p.poll() is SilenceAction.REPROMPT


def test_max_reprompts_zero_goes_straight_to_end(clock: FakeClock) -> None:
    p = policy(clock, max_reprompts=0)
    agent_finishes_talking(p)
    clock.advance(6)
    assert p.poll() is None
    clock.advance(9)
    assert p.poll() is SilenceAction.END


def test_two_reprompts(clock: FakeClock) -> None:
    p = SilencePolicy(reprompt_after_s=5, end_after_s=30, max_reprompts=2, reprompt_every_s=5, clock=clock)
    agent_finishes_talking(p)
    clock.advance(5)
    assert p.poll() is SilenceAction.REPROMPT
    clock.advance(4.9)
    assert p.poll() is None
    clock.advance(0.1)
    assert p.poll() is SilenceAction.REPROMPT
    clock.advance(10)
    assert p.poll() is None


def test_deadline_tells_the_poller_when_to_look_again(clock: FakeClock) -> None:
    p = policy(clock)
    assert p.next_deadline() is None
    agent_finishes_talking(p)
    assert p.next_deadline() == clock.now + 6
    clock.advance(6)
    assert p.poll() is SilenceAction.REPROMPT
    assert p.next_deadline() == pytest.approx(clock.now + 9)
    p.on_agent_state("speaking")
    assert p.next_deadline() is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reprompt_after_s": 0, "end_after_s": 15},
        {"reprompt_after_s": 6, "end_after_s": 6},
        {"reprompt_after_s": 6, "end_after_s": 15, "max_reprompts": -1},
    ],
)
def test_invalid_thresholds_are_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        SilencePolicy(**kwargs)


# ------------------------------------------------------------------ attach_silence_policy


class FakeSession:
    """Just the `on`/`off` event surface of `AgentSession`."""

    def __init__(self) -> None:
        self.handlers: dict[str, list[Callable[[Any], None]]] = {}

    def on(self, event: str, callback: Callable[[Any], None] | None = None) -> Any:
        self.handlers.setdefault(event, []).append(callback)  # type: ignore[arg-type]
        return callback

    def off(self, event: str, callback: Callable[[Any], None]) -> None:
        self.handlers[event].remove(callback)

    def emit(self, event: str, new_state: str) -> None:
        class Ev:
            pass

        ev = Ev()
        ev.new_state = new_state  # type: ignore[attr-defined]
        for cb in list(self.handlers.get(event, [])):
            cb(ev)


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.002)


async def test_attach_drives_the_callbacks_from_session_events(clock: FakeClock) -> None:
    session = FakeSession()
    calls: list[str] = []

    async def on_reprompt() -> None:
        calls.append("reprompt")

    def on_end(reason: str) -> None:  # a plain function works too
        calls.append(f"end:{reason}")

    handle = attach_silence_policy(session, policy(clock), on_reprompt, on_end, tick_s=0.001)  # type: ignore[arg-type]
    try:
        session.emit("agent_state_changed", "speaking")
        session.emit("agent_state_changed", "listening")
        clock.advance(6)
        await settle()
        assert calls == ["reprompt"]
        clock.advance(9)
        await settle()
        assert calls == ["reprompt", "end:silence"]
    finally:
        handle.stop()


async def test_stop_detaches_and_ends_the_poller(clock: FakeClock) -> None:
    session = FakeSession()
    calls: list[str] = []
    handle = attach_silence_policy(
        session,  # type: ignore[arg-type]
        policy(clock),
        lambda: calls.append("reprompt"),
        lambda reason: calls.append(reason),
        tick_s=0.001,
    )
    handle.stop()
    assert all(not hs for hs in session.handlers.values())
    session.emit("agent_state_changed", "listening")
    clock.advance(60)
    await settle()
    assert calls == []


async def test_a_failing_callback_does_not_kill_the_poller(clock: FakeClock) -> None:
    session = FakeSession()
    calls: list[str] = []

    def boom() -> None:
        raise RuntimeError("tts down")

    handle = attach_silence_policy(
        session,  # type: ignore[arg-type]
        policy(clock),
        boom,
        lambda reason: calls.append(reason),
        tick_s=0.001,
    )
    try:
        session.emit("agent_state_changed", "speaking")
        session.emit("agent_state_changed", "listening")
        clock.advance(6)
        await settle()
        clock.advance(10)
        await settle()
        assert calls == ["silence"]
    finally:
        handle.stop()
