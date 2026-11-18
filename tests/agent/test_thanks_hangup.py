"""AGENT-R1 · the fourth real playground call (2026-11-18): the caller "asked a question" and the transcript
holds only "Thank you.", "Thanks. / All right." and "Thank you."; the call then closed with `caller_goodbye`.

whisper.cpp writes "Thank you." for noise (see tests/voice/test_whisper_stt.py), so a bare thanks is the most
likely thing the router will ever hear that is not a question. It must never end a call unless the agent has
just SAID "anything else?". Synthetic data.
"""

import logging
from pathlib import Path

import pytest

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent.states import flow
from supportagent.agent.states.greeting import GreetingAgent
from supportagent.agent.states.router import ANYTHING_ELSE, CLARIFY, HOW_CAN_I_HELP, RouterAgent
from supportagent.agent.testing import FakeGateway, ScriptedLLM, call, run_text_scenario, say
from supportagent.contracts.outcome import Campaign, Direction
from tests.agent.test_third_call_shape import FRANK, REGISTRY, VARS, _lines, _run

ROOT = Path(__file__).resolve().parents[2]
THANKS = ["Thank you.", "Thanks. All right.", "Thank you."]


async def _from_greeting(llm: ScriptedLLM, turns: list[str]):  # type: ignore[no-untyped-def]
    async def on_start(state) -> None:  # type: ignore[no-untyped-def]
        state.disclosures = REGISTRY

    return await run_text_scenario(
        FRANK,
        turns,
        agent=GreetingAgent(variables=VARS),
        llm=llm,
        gateway=FakeGateway(FRANK),
        direction=Direction.inbound,
        campaign=Campaign.inbound,
        on_start=on_start,
    )


async def test_the_real_calls_thanks_never_end_the_call() -> None:
    llm = ScriptedLLM([say("Sorry?")] * 6)
    result = await _from_greeting(llm, THANKS)
    assert "closing" not in result.states
    assert result.state.end_reason is None
    assert _lines(result).count(CLARIFY) == 2  # asked in code, twice, then the model takes over


async def test_even_if_the_model_calls_finish_call_on_thanks_the_call_stays_open() -> None:
    llm = ScriptedLLM([call("finish_call"), say("What can I help with?")] * 3)
    result = await _from_greeting(llm, THANKS)
    assert "closing" not in result.states
    assert result.state.end_reason is None
    refused = [c for c in result.tool_calls if c.name == "finish_call"]
    assert refused and all(flow.NOT_DONE in (c.output or "") for c in refused)


@pytest.mark.parametrize("turn", ["Goodbye.", "That's all, thanks.", "Thanks, bye."])
async def test_a_real_goodbye_still_ends_the_call(turn: str) -> None:
    result = await _from_greeting(ScriptedLLM([say("unused")]), [turn])
    assert result.states[-1] == "closing"
    assert result.state.end_reason == flow.END_BY_CALLER


async def test_thanks_after_a_spoken_anything_else_ends_the_call() -> None:
    llm = ScriptedLLM(
        [
            call("route_policy_question"),
            call("search_policy", query="late fees"),
            say("It is $25."),
            call("finish"),
        ]
    )
    result = await _run(llm, ["What is the late fee?", "Okay, I see.", "Thank you."])
    assert ANYTHING_ELSE in _lines(result)
    assert result.states[-1] == "closing"


async def test_the_flag_is_set_exactly_when_anything_else_is_said() -> None:
    first = await _from_router(RouterAgent(variables=VARS), [], returning=False)
    assert _lines(first)[0] == HOW_CAN_I_HELP and first.state.asked_anything_else is False
    back = await _from_router(RouterAgent(variables=VARS), [], returning=True)
    assert _lines(back)[0] == ANYTHING_ELSE and back.state.asked_anything_else is True


async def test_the_flag_is_consumed_by_the_next_turn() -> None:
    """A turn that is not a goodbye uses the flag up: a later bare thanks is then just thanks."""
    result = await _from_router(
        RouterAgent(variables=VARS), ["I would like some help.", "Thank you."], returning=True
    )
    assert "closing" not in result.states
    assert result.state.asked_anything_else is False


async def _from_router(agent, turns, *, returning):  # type: ignore[no-untyped-def]
    async def on_start(state) -> None:  # type: ignore[no-untyped-def]
        state.disclosures = REGISTRY
        if returning:
            state.states_visited.extend(["greeting", "router"])

    return await run_text_scenario(
        FRANK,
        turns,
        agent=agent,
        llm=ScriptedLLM([say("Sorry?")] * 4),
        gateway=FakeGateway(FRANK),
        direction=Direction.inbound,
        campaign=Campaign.inbound,
        on_start=on_start,
    )


async def test_the_closing_decision_is_logged_without_the_callers_words(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    await _from_greeting(ScriptedLLM([say("unused")]), ["Goodbye, my secret phrase."])
    messages = [r for r in caplog.records if "farewell by phrase" in r.getMessage()]
    assert messages, "the path that closed the call must be logged"
    assert "secret" not in caplog.text
