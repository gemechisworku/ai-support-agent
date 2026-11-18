"""Caller-silence policy (VOICE-R5): re-prompt once, then end the call politely.

`SilencePolicy` is a pure state machine with an injected clock. It is fed the session's
`user_state_changed` / `agent_state_changed` states and asked `poll()` what to do:

- Silence is measured in seconds of quiet: it counts while the agent is not speaking or thinking and the
  caller is not speaking, and it starts when the agent finishes a turn or the caller stops talking.
- Only the *caller* speaking resets it (and restores the re-prompt budget), or the agent replying to a
  caller turn. The agent's own speech, such as the re-prompt, neither counts as silence nor as caller
  activity, so `end_after_s` is total caller silence.
- No action is ever emitted while the agent is speaking or thinking (no re-prompt over its own speech).
- `hold(True)` also pauses the clock. The agent's *state* can flicker to `listening` for a moment between
  `thinking` stages while a reply is still being generated or queued (seen on the first real call: a re-prompt
  was queued behind a reply that took 80 s). `attach_silence_policy` therefore holds the clock whenever the
  session still has speech in flight (`is_busy`).
- `set_closing(True)` (the call is wrapping up: the agent has said, or is about to say, goodbye) suppresses
  re-prompts for good; the end of the call still fires if the caller says nothing. `close()` stops the policy.
- `poll()` returns `SilenceAction.REPROMPT` (at most `max_reprompts` times) and then `SilenceAction.END`
  (once), or `None`.

The agent supplies the wording of the re-prompt and of the closing line (T-AGENT-08);
`attach_silence_policy` wires a policy to a LiveKit `AgentSession` and calls back into the agent:
`on_reprompt()` and `on_end("silence")`. It uses no LiveKit imports: any object with `on`/`off` works.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from supportagent.common.logging import get_logger

log = get_logger(__name__)

END_REASON = "silence"
BUSY_AGENT_STATES = frozenset({"speaking", "thinking"})


class SilenceAction(StrEnum):
    REPROMPT = "reprompt"
    END = "end"


class SilencePolicy:
    def __init__(
        self,
        reprompt_after_s: float,
        end_after_s: float,
        max_reprompts: int = 1,
        *,
        reprompt_every_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if reprompt_after_s <= 0:
            raise ValueError("reprompt_after_s must be positive")
        if end_after_s <= reprompt_after_s:
            raise ValueError("end_after_s must be greater than reprompt_after_s")
        if max_reprompts < 0:
            raise ValueError("max_reprompts must be >= 0")
        every = reprompt_after_s if reprompt_every_s is None else reprompt_every_s
        if every <= 0:
            raise ValueError("reprompt_every_s must be positive")
        self.reprompt_after_s = reprompt_after_s
        self.end_after_s = end_after_s
        self.max_reprompts = max_reprompts
        self.reprompt_every_s = every
        self._clock = clock
        self._armed = False  # a silence period is in progress (the agent has spoken or the caller stopped)
        self._acc = 0.0  # caller silence accumulated so far in this period, in seconds
        self._since: float | None = None  # when the silence started counting again, if it is counting now
        self._caller_turn = False  # the period began with the caller stopping: the agent's reply restarts it
        self._reprompts = 0
        self._agent_busy = False
        self._user_speaking = False
        self._hold = False
        self._closing = False
        self._ended = False

    # ------------------------------------------------------------------ state inputs

    def _refresh(self) -> None:
        """Silence counts only while a period is armed, the agent is not talking and the caller is not."""
        now = self._clock()
        counting = self._armed and not self._agent_busy and not self._user_speaking and not self._hold
        if counting and self._since is None:
            self._since = now
        elif not counting and self._since is not None:
            self._acc += now - self._since
            self._since = None

    def on_user_state(self, state: str) -> None:
        """`speaking`, `listening` or `away` (LiveKit's `UserState`)."""
        was_speaking = self._user_speaking
        if state == "speaking":
            self._refresh()
            self._user_speaking = True
            self._armed = False
            self._caller_turn = False
            self._acc = 0.0
            self._since = None
            self._reprompts = 0  # caller speech restores the re-prompt budget
            return
        self._user_speaking = False
        if was_speaking:  # the caller just stopped: the clock starts, until the agent replies
            self._armed = True
            self._caller_turn = True
        self._refresh()

    def on_agent_state(self, state: str) -> None:
        """`initializing`, `idle`, `listening`, `thinking` or `speaking` (LiveKit's `AgentState`)."""
        busy = state in BUSY_AGENT_STATES
        if busy and not self._agent_busy:
            self._agent_busy = True
            self._refresh()  # pause the clock: the agent's own speech is neither caller activity nor silence
            if self._caller_turn:  # this is the reply to the caller: silence is measured after it
                self._acc = 0.0
                self._caller_turn = False
        elif not busy and self._agent_busy:
            self._agent_busy = False
            self._armed = True  # the agent finished a turn (greeting, answer or re-prompt)
            self._refresh()

    def hold(self, on: bool) -> None:
        """Pause (or resume) the clock because something else is still going on: speech in flight."""
        if on != self._hold:
            self._refresh()  # bank the silence counted so far under the old setting
            self._hold = on
            self._refresh()

    def set_closing(self, closing: bool = True) -> None:
        """The call is wrapping up: no more re-prompts (the end of the call can still fire)."""
        self._closing = closing

    def close(self) -> None:
        """Stop the policy for good (the call is ending for another reason): `poll()` returns None."""
        self._ended = True
        self._since = None

    # ------------------------------------------------------------------ decisions

    def _elapsed(self) -> float:
        return self._acc + (self._clock() - self._since if self._since is not None else 0.0)

    def _active(self) -> bool:
        return not self._ended and self._since is not None

    def _reprompt_at(self) -> float | None:
        """Silence (seconds) at which the next re-prompt is due, or None when the budget is spent."""
        if self._closing or self._reprompts >= self.max_reprompts:
            return None
        return self.reprompt_after_s + self._reprompts * self.reprompt_every_s

    def next_deadline(self) -> float | None:
        """Clock time of the next action, or None when nothing is pending (the agent is talking, etc.)."""
        if not self._active():
            return None
        due = self._reprompt_at()
        remaining = self.end_after_s if due is None else min(due, self.end_after_s)
        return self._clock() + remaining - self._elapsed()

    def poll(self) -> SilenceAction | None:
        if not self._active():
            return None
        elapsed = self._elapsed()
        if elapsed >= self.end_after_s:
            self._ended = True
            return SilenceAction.END
        due = self._reprompt_at()
        if due is not None and elapsed >= due:
            self._reprompts += 1
            return SilenceAction.REPROMPT
        return None


# ---------------------------------------------------------------------- wiring to a session


class _Emitter(Protocol):
    def on(self, event: str, callback: Callable[..., Any] | None = None) -> Any: ...

    def off(self, event: str, callback: Callable[..., Any]) -> Any: ...


@dataclass
class SilenceHandle:
    """Returned by `attach_silence_policy`; `stop()` detaches the handlers and cancels the poller."""

    _session: _Emitter
    _handlers: list[tuple[str, Callable[..., Any]]]
    _task: asyncio.Task[None]

    def stop(self) -> None:
        for event, callback in self._handlers:
            with contextlib.suppress(Exception):  # already detached, or the session is closing
                self._session.off(event, callback)
        self._handlers.clear()
        self._task.cancel()


async def _call(callback: Callable[..., Any], *args: Any) -> None:
    result = callback(*args)
    if inspect.isawaitable(result):
        await result


def attach_silence_policy(
    session: _Emitter,
    policy: SilencePolicy,
    on_reprompt: Callable[[], Awaitable[None] | None],
    on_end: Callable[[str], Awaitable[None] | None],
    *,
    tick_s: float = 0.25,
    is_busy: Callable[[], bool] | None = None,
    is_closing: Callable[[], bool] | None = None,
) -> SilenceHandle:
    """Feed `policy` from `session`'s state events and run it; must be called inside a running event loop.

    `on_reprompt()` should make the agent say its re-prompt line; `on_end("silence")` should say the closing
    line and end the call. A callback that raises is logged (without content) and the policy carries on.

    `is_busy()` says whether the session still has speech in flight (a reply being generated or spoken,
    a queued
    line): the clock is held while it is true, so the policy never re-prompts over a reply that has not been
    delivered yet. `is_closing()` says the call is wrapping up: re-prompts stop for good.
    """

    def user_changed(event: Any) -> None:
        policy.on_user_state(event.new_state)

    def agent_changed(event: Any) -> None:
        policy.on_agent_state(event.new_state)

    session.on("user_state_changed", user_changed)
    session.on("agent_state_changed", agent_changed)

    async def run() -> None:
        while True:
            policy.hold(bool(is_busy()) if is_busy is not None else False)
            if is_closing is not None and is_closing():
                policy.set_closing(True)
            action = policy.poll()
            if action is SilenceAction.REPROMPT:
                try:
                    await _call(on_reprompt)
                except Exception:
                    log.exception("silence re-prompt callback failed")
            elif action is SilenceAction.END:
                try:
                    await _call(on_end, END_REASON)
                except Exception:
                    log.exception("silence end callback failed")
                return
            await asyncio.sleep(tick_s)

    task = asyncio.get_running_loop().create_task(run(), name="silence-policy")
    return SilenceHandle(
        session,
        [("user_state_changed", user_changed), ("agent_state_changed", agent_changed)],
        task,
    )
