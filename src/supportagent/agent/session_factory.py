"""Builds the call's `AgentSession` and wires it to the states, the silence policy and the outcome recorder.

The worker (`worker.run_call`) uses these in this order:

    session = build_agent_session(settings, vad, state, turn_detector=turn_detector)
    wiring = wire_call(session, state, settings, on_close=...)     # recorder, silence policy, sentiment
    await session.start(entry_agent(settings), room=room)          # the greeting speaks the disclosure first

- `build_agent_session` is pure: STT, LLM and TTS come from the T-VOICE-02 providers and the turn-taking
  options from T-VOICE-03 (`build_session_options`); the `CallState` is the session's `userdata`.
- `wire_call` attaches what listens to the session:
  - the recorder (`finalize.attach_recorder`) keeps every turn and tool call for the transcript store;
  - the silence policy (`voice.silence`) re-prompts once, then says the closing line and ends the call. The
    wording is the agent's: `REPROMPT_LINE` and `closing.goodbye_line`. The end reason is kept on
    `CallState.end_reason` and written in the transcript's call-ended marker;
  - the sentiment classifier (`finalize.llm_sentiment_classifier`) asks the call's own model, at the end;
  - `on_close` is called when the session closes, so the worker can end the job (the shutdown callback then
    writes the outcome).
- Everything takes the session as an argument, so tests drive it with a text-mode `AgentSession` or a fake.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from livekit.agents import AgentSession
from livekit.agents import llm as lk_llm
from livekit.agents import vad as lk_vad

from supportagent.agent.base import prompt_variables
from supportagent.agent.context import CallState
from supportagent.agent.finalize import SentimentClassifier, attach_recorder, llm_sentiment_classifier
from supportagent.agent.states.closing import goodbye_line
from supportagent.agent.states.greeting import GreetingAgent
from supportagent.common.logging import get_logger
from supportagent.common.settings import Settings
from supportagent.voice.session import build_session_options
from supportagent.voice.silence import SilenceHandle, SilencePolicy, attach_silence_policy

log = get_logger(__name__)

# What the agent says when the caller has been quiet for SILENCE_REPROMPT_S. No account data, no names.
REPROMPT_LINE = "Hello, are you still there?"


@dataclass
class CallWiring:
    """What `wire_call` attached to a session; `stop()` detaches the silence policy."""

    silence: SilenceHandle
    classify: SentimentClassifier | None

    def stop(self) -> None:
        self.silence.stop()


def build_agent_session(
    settings: Settings, vad: lk_vad.VAD, state: CallState, *, turn_detector: Any | None = None
) -> AgentSession[CallState]:
    """The call's `AgentSession`: providers and turn-taking from `Settings`, the call state as `userdata`.

    `vad` and `turn_detector` are the ones the worker prewarmed (loading them costs seconds per call).
    """
    options = build_session_options(settings, vad=vad, turn_detector=turn_detector)
    return AgentSession[CallState](userdata=state, **options)


def entry_agent(settings: Settings) -> GreetingAgent:
    """The first state of every call: the greeting, which speaks the recording disclosure first."""
    return GreetingAgent(variables=prompt_variables(settings))


def silence_policy(settings: Settings) -> SilencePolicy:
    m = settings.models
    return SilencePolicy(m.silence_reprompt_s, m.silence_end_s)


def wire_call(
    session: Any,
    state: CallState,
    settings: Settings,
    *,
    policy: SilencePolicy | None = None,
    tick_s: float = 0.25,
    classify: SentimentClassifier | None = None,
    on_close: Callable[[], None] | None = None,
) -> CallWiring:
    """Attach the recorder, the silence policy, the sentiment classifier and the close hook to `session`.

    Must run inside the event loop (the silence poller is a task). Call it before `session.start`.
    """
    attach_recorder(session, state)
    bank_name = settings.persona.bank_name

    def on_reprompt() -> None:
        session.say(REPROMPT_LINE)

    def on_end(reason: str) -> None:
        state.end_reason = reason
        session.say(goodbye_line(bank_name), allow_interruptions=False)
        session.shutdown(drain=True)  # drain: the goodbye is spoken before the session closes

    silence = attach_silence_policy(
        session, policy or silence_policy(settings), on_reprompt, on_end, tick_s=tick_s
    )

    if on_close is not None:

        def closed(_event: Any) -> None:
            on_close()

        session.on("close", closed)

    if classify is None and isinstance(getattr(session, "llm", None), lk_llm.LLM):
        classify = llm_sentiment_classifier(session.llm)
    return CallWiring(silence=silence, classify=classify)
