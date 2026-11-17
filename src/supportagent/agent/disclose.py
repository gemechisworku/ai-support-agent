"""Speak a disclosure verbatim from the versioned registry and record its tag (AGENT-R4, COMP-R5).

The words come from `supportagent.compliance.disclosures` (`data/config/disclosures.yaml`), never from the
model: `speak_disclosure` renders the registry text and says it with `session.say` (so the model cannot
paraphrase it). The speech is NOT interruptible: on the first real call the caller's first words cancelled the
recording disclosure before the TTS had produced any audio, so it was never spoken and never recorded in the
transcript. `key@version` is appended to `CallState.disclosures_given` (the outcome's
`disclosures_given`) only
once the speech has finished without being interrupted, so the outcome never claims a disclosure that the
caller did not hear. The check after the call is `compliance.check_disclosures`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from livekit.agents.voice.speech_handle import SpeechHandle

from supportagent.agent.context import CallState
from supportagent.common.logging import get_logger
from supportagent.compliance.disclosures import DisclosureRegistry, load_disclosures

if TYPE_CHECKING:
    from supportagent.agent.base import BaseStateAgent

log = get_logger(__name__)


def registry_for(state: CallState) -> DisclosureRegistry:
    """The call's disclosure registry: the one set on the state, else the repo's file (loaded once)."""
    if state.disclosures is None:
        state.disclosures = load_disclosures()
    return state.disclosures


def speak_disclosure(agent: BaseStateAgent, key: str, **values: str) -> SpeechHandle:
    """Say disclosure `key` verbatim and record `key@version` (once per call). Returns the speech handle.

    `{bank_name}` and `{agent_name}` come from the state's prompt variables; `values` adds the rest
    (for example `first_name`).
    """
    state = agent.state
    registry = registry_for(state)
    rendered = registry.render(key, **{**agent.variables, **values})
    handle = agent.session.say(rendered.text, allow_interruptions=False)
    tag = registry.tag(key)

    def spoken(done: SpeechHandle) -> None:
        if done.interrupted:  # not heard in full: it must not count as given
            log.warning("disclosure interrupted", extra={"disclosure": tag, "state": agent.state_name})
            return
        if tag not in state.disclosures_given:
            state.disclosures_given.append(tag)
        log.info("disclosure spoken", extra={"disclosure": tag, "state": agent.state_name})

    handle.add_done_callback(spoken)
    return handle
