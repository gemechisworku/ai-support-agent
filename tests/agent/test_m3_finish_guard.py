"""AGENT-R7/R8/R10 · the third real call (2026-11-18) hung up on "Mm-hmm.": the model called an end-of-call
tool on filler. The router, policy and account states were guarded in T-AGENT-05; this covers the reminder,
the collections states and the callback state the same way: `finish` closes only when the caller's latest
turn is a farewell (or the business is already done: a booked callback, a recorded intent or promise), an
empty turn gets no reply, "what can you do?" is answered in code and the model never speaks a quoted prompt
example. Synthetic data; the scenarios reuse the reminder and callback test fixtures.
"""

from typing import Any

import pytest

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent.prompts.registry import DEFAULT_PROMPTS_DIR, PromptRegistry
from supportagent.agent.states import flow
from supportagent.agent.testing import call, say
from supportagent.compliance.disclosures import DisclosureRegistry
from tests.agent import test_callback as cb
from tests.agent import test_collections as col
from tests.agent import test_reminder as rem

pytestmark = pytest.mark.asyncio


@pytest.fixture
def prompts() -> PromptRegistry:
    return PromptRegistry(DEFAULT_PROMPTS_DIR)


@pytest.fixture
def registry() -> DisclosureRegistry:
    return cb.load_disclosures(cb.ROOT / "data" / "config" / "disclosures.yaml")


def _finish_outputs(result: Any) -> list[str]:
    return [c.output or "" for c in result.tool_calls if c.name == "finish"]


# --------------------------------------------------------------------------- the reminder state


async def test_mm_hmm_does_not_let_the_model_end_a_reminder_call(prompts: PromptRegistry) -> None:
    result = await rem._run(
        prompts,
        [say("Can you pay by the due date?"), call("finish"), say("Okay. Can you pay by the due date?")],
        {"get_loan_status": rem._status(rem.PERSONAL), "search_policy": rem.CHANNELS},
        turns=["Mm-hmm."],
    )
    assert "closing" not in result.states
    assert result.state.end_reason is None
    assert _finish_outputs(result) and flow.NOT_DONE in _finish_outputs(result)[0]
    assert flow.NOT_DONE not in " ".join(rem._spoken(result))  # the model sees it; the caller never hears it


async def test_a_done_statement_lets_finish_close_the_reminder_call(prompts: PromptRegistry) -> None:
    result = await rem._run(
        prompts,
        [say("Can you pay by the due date?"), call("finish"), say("Is there anything else?")],
        {"get_loan_status": rem._status(rem.PERSONAL), "search_policy": rem.CHANNELS},
        turns=["I think we are finished here then."],
    )
    assert result.states[-1] == "closing"


async def test_finish_closes_the_reminder_once_a_callback_is_booked(prompts: PromptRegistry) -> None:
    """The business is done (a callback the gateway booked), so `finish` needs no farewell."""
    result = await rem._run(
        prompts,
        [
            say("Can you pay by the due date?"),
            call("offer_callback"),
            call("finish"),
            say("Is there anything else?"),
        ],
        {
            "get_loan_status": rem._status(rem.PERSONAL),
            "search_policy": rem.CHANNELS,
            "request_callback": rem.TASK,
        },
        turns=["Please have someone call me tomorrow."],
    )
    assert result.state.outcome.callback is not None
    assert result.states[-1] == "closing"


async def test_a_capabilities_question_is_answered_in_code_in_the_reminder(prompts: PromptRegistry) -> None:
    result = await rem._run(
        prompts,
        [say("Can you pay by the due date?")],
        {"get_loan_status": rem._status(rem.PERSONAL), "search_policy": rem.CHANNELS},
        turns=["What can you do for me?"],
    )
    assert flow.CAPABILITIES_LINE in rem._spoken(result)
    assert result.llm.remaining == 0  # the model was not asked


async def test_an_empty_turn_gets_no_reply_in_the_reminder(prompts: PromptRegistry) -> None:
    result = await rem._run(
        prompts,
        [say("Can you pay by the due date?")],
        {"get_loan_status": rem._status(rem.PERSONAL), "search_policy": rem.CHANNELS},
        turns=["..."],  # what the STT non-speech filter leaves behind
    )
    assert result.llm.remaining == 0
    # The reminder line (code) and the model's opening question: nothing was said over the noise.
    assert len(rem._spoken(result)) == 2


async def test_the_reminder_never_speaks_a_recited_prompt_example(prompts: PromptRegistry) -> None:
    result = await rem._run(
        prompts,
        [say("Can you pay by the due date?"), say("That's all.")],
        {"get_loan_status": rem._status(rem.PERSONAL), "search_policy": rem.CHANNELS},
        turns=["Tell me a little more please."],
    )
    assert "That's all." not in rem._spoken(result)
    assert flow.ASK_MORE in rem._spoken(result)


# --------------------------------------------------------------------------- the callback state


def _callback_results(**extra: object) -> dict[str, object]:
    return {
        "get_interaction_history": cb._history(cb.OPEN),
        "get_loan_status": cb._status(cb.OVERDUE_LOAN),
        **extra,
    }


async def test_mm_hmm_does_not_let_the_model_end_a_callback_call(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await cb._run(
        prompts,
        registry,
        [call("finish"), say("Okay. What date can you pay by?")],
        _callback_results(),
        turns=["Mm-hmm."],
    )
    assert "closing" not in result.states
    assert result.state.end_reason is None
    assert _finish_outputs(result) and flow.NOT_DONE in _finish_outputs(result)[0]


async def test_a_done_statement_lets_finish_close_the_callback_call(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await cb._run(
        prompts,
        registry,
        [call("finish"), say("Is there anything else?")],
        _callback_results(),
        turns=["I think we are finished here then."],
    )
    assert result.states[-1] == "closing"


async def test_finish_closes_the_callback_state_once_a_callback_is_booked(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await cb._run(
        prompts,
        registry,
        [call("offer_callback"), call("finish"), say("Is there anything else?")],
        _callback_results(request_callback=cb.TASK),
        turns=["Please have someone call me."],
    )
    assert result.state.outcome.callback is not None
    assert result.states[-1] == "closing"


async def test_a_capabilities_question_is_answered_in_code_in_the_callback_state(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await cb._run(prompts, registry, [], _callback_results(), turns=["What can you do for me?"])
    assert flow.CAPABILITIES_LINE in cb._spoken(result)
    assert result.llm.remaining == 0


async def test_an_empty_turn_gets_no_reply_in_the_callback_state(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    quiet = await cb._run(prompts, registry, [], _callback_results(), turns=[])
    noisy = await cb._run(prompts, registry, [], _callback_results(), turns=["..."])
    assert cb._spoken(noisy) == cb._spoken(quiet)  # nothing said over the noise
    assert noisy.llm.remaining == 0


async def test_the_callback_state_never_speaks_a_recited_prompt_example(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await cb._run(
        prompts, registry, [say("No thanks.")], _callback_results(), turns=["Tell me a little more please."]
    )
    assert "No thanks." not in cb._spoken(result)
    assert flow.ASK_MORE in cb._spoken(result)


# --------------------------------------------------------------------------- the collections states


async def test_a_capabilities_question_is_answered_in_code_in_the_collections_resolve_state(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await col._run(
        prompts, registry, col._resolve(prompts), [], {}, turns=["What can you do for me?"]
    )
    assert flow.CAPABILITIES_LINE in col._spoken(result)
    assert result.llm.remaining == 0


async def test_the_collections_resolve_state_never_speaks_a_recited_prompt_example(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await col._run(
        prompts,
        registry,
        col._resolve(prompts),
        [say("That's all.")],
        {},
        turns=["Tell me a little more please."],
    )
    assert "That's all." not in col._spoken(result)
    assert flow.ASK_MORE in col._spoken(result)
