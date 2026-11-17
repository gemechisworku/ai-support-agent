"""Speak a disclosure verbatim from the versioned registry and record its tag (AGENT-R4, COMP-R5).

The words come from `supportagent.compliance.disclosures` (`data/config/disclosures.yaml`), never from the
model: `speak_disclosure` renders the registry text, says it with `session.say` (so the model cannot
paraphrase it) and appends `key@version` to `CallState.disclosures_given`, which becomes the outcome's
`disclosures_given`. The check after the call is `compliance.check_disclosures`.
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
    handle = agent.session.say(rendered.text)
    tag = registry.tag(key)
    if tag not in state.disclosures_given:
        state.disclosures_given.append(tag)
    log.info("disclosure spoken", extra={"disclosure": tag, "state": agent.state_name})
    return handle
