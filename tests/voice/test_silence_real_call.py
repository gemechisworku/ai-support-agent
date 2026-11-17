"""VOICE-R5 · regression: the silence policy on the turn-taking of the first real playground call
(2026-11-17).

`EVENTS` is a synthetic copy of that call's `session_events.jsonl`: only states and seconds since the
first event
(no speech, nothing recorded from the caller). The agent's own re-prompt utterances (3 of them) are
left out so
the replay shows only what the caller and the replies did, and what a policy would decide on its own.
On the real
call the agent said "Hello, are you still there?" four times in a conversation the caller was following, once
after a reply that had not been delivered yet.

What the replay shows, with the old 6 s / 15 s thresholds and no knowledge of speech in flight:
- three of the four re-prompts came exactly 6 s after the agent finished a reply: the caller was listening and
  took 8-11 s to answer;
- the agent state flickered `thinking -> listening -> thinking` while a reply was still being
  generated, so the
  policy saw "idle" and re-prompted at 242 s, over a reply that took until 327 s.
"""

from collections.abc import Callable

from supportagent.voice.silence import SilenceAction, SilencePolicy

U, A = "user_state_changed", "agent_state_changed"

EVENTS: list[tuple[float, str, str]] = [
    (0.0, A, "listening"),
    (0.9, U, "speaking"),
    (1.7, U, "listening"),
    (6.0, U, "speaking"),
    (7.7, U, "listening"),
    (10.3, A, "speaking"),
    (11.7, A, "listening"),
    (15.1, U, "speaking"),
    (17.8, U, "listening"),
    (19.1, A, "thinking"),
    (50.5, A, "listening"),  # flicker: the reply is still being generated
    (50.6, A, "thinking"),
    (76.2, A, "speaking"),
    (83.3, A, "listening"),
    (94.7, U, "speaking"),
    (97.6, U, "listening"),
    (98.6, U, "speaking"),
    (99.5, U, "listening"),
    (100.2, A, "thinking"),
    (101.2, A, "listening"),  # flicker
    (101.6, A, "thinking"),
    (118.1, A, "speaking"),
    (124.1, A, "listening"),
    (132.4, U, "speaking"),
    (141.0, U, "listening"),
    (142.6, A, "thinking"),
    (148.1, A, "speaking"),
    (166.7, A, "listening"),
    (176.4, U, "speaking"),
    (184.0, U, "listening"),
    (185.4, A, "thinking"),
    (204.5, A, "speaking"),
    (216.6, A, "listening"),
    (218.8, U, "speaking"),
    (232.7, U, "listening"),
    (233.3, U, "speaking"),
    (234.7, U, "listening"),
    (235.3, A, "thinking"),
    (236.1, A, "listening"),  # flicker: no speech until 327.8
    (320.5, U, "speaking"),
    (322.6, U, "listening"),
    (323.9, A, "thinking"),
    (327.8, A, "speaking"),
    (334.8, A, "listening"),
    (337.3, U, "speaking"),
    (338.4, U, "listening"),
    (339.6, A, "thinking"),
    (341.5, A, "speaking"),
    (342.5, A, "listening"),
]


def speech_in_flight() -> Callable[[float], bool]:
    """What `AgentSession.current_speech` reports: a reply exists from `thinking` until it has been spoken."""
    spans: list[tuple[float, float]] = []
    start: float | None = None
    spoke = False
    for t, kind, state in EVENTS:
        if kind != A:
            continue
        if state == "thinking" and start is None:
            start, spoke = t, False
        elif state == "speaking":
            if start is None:
                start = t
            spoke = True
        elif state == "listening" and start is not None and spoke:
            spans.append((start, t))
            start = None
    return lambda t: any(a <= t <= b for a, b in spans)


def replay(
    reprompt_after_s: float, end_after_s: float, *, hold: bool, closing_from: float | None = None
) -> list[tuple[float, str]]:
    """Feed EVENTS to a SilencePolicy on a fake clock (0.25 s ticks) and return what it decided and when."""
    now = [0.0]
    policy = SilencePolicy(reprompt_after_s, end_after_s, clock=lambda: now[0])
    in_flight = speech_in_flight()
    actions: list[tuple[float, str]] = []
    i, t, last = 0, 0.0, EVENTS[-1][0] + 1  # the real caller hung up right after the last event
    while t <= last:
        while i < len(EVENTS) and EVENTS[i][0] <= t:
            now[0] = EVENTS[i][0]
            _, kind, state = EVENTS[i]
            (policy.on_user_state if kind == U else policy.on_agent_state)(state)
            i += 1
        now[0] = t
        if hold:
            policy.hold(in_flight(t))
        if closing_from is not None and t >= closing_from:
            policy.set_closing(True)
        action = policy.poll()
        if action is not None:
            actions.append((round(t, 2), action.value))
        t += 0.25
    return actions


def test_the_old_behaviour_reprompted_four_times_and_over_an_undelivered_reply() -> None:
    """Pins the bug: no knowledge of speech in flight, 6 s threshold."""
    actions = replay(6, 15, hold=False)
    reprompts = [t for t, a in actions if a == SilenceAction.REPROMPT]
    assert len(reprompts) == 4  # the four "Hello, are you still there?" of the real transcript
    assert 235.3 < reprompts[-1] < 327.8  # fired while a reply was still being generated


def test_no_reprompt_or_end_while_a_reply_is_in_flight() -> None:
    for thresholds in ((6, 15), (10, 25)):
        actions = replay(*thresholds, hold=True)
        assert [t for t, _ in actions if 235.3 <= t <= 334.8] == []


def test_the_new_thresholds_leave_a_following_caller_alone() -> None:
    """With 10 s / 25 s only the one pause of more than 10 s (11.4 s after a reply) is re-prompted, and
    the call is
    never ended: the caller answered every time."""
    actions = replay(10, 25, hold=True)
    assert [a for _, a in actions] == ["reprompt"]
    assert 90 < actions[0][0] < 95


def test_the_flicker_does_not_restart_the_silence_clock() -> None:
    """Without the hold the 236 s flicker armed the clock and the policy would even have ended the call."""
    without = replay(10, 25, hold=False)
    with_hold = replay(10, 25, hold=True)
    assert any(a == SilenceAction.END for _, a in without)
    assert all(a != SilenceAction.END for _, a in with_hold)


def test_nothing_is_reprompted_once_the_call_is_wrapping_up() -> None:
    """After the goodbye / the closing state no re-prompt may follow (it was said after "Goodbye!" before)."""
    actions = replay(6, 15, hold=True, closing_from=118.1)
    assert [t for t, a in actions if t >= 118.1 and a == SilenceAction.REPROMPT] == []
