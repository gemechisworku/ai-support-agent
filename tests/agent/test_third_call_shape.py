"""AGENT-R1 · the third real playground call (2026-11-18), replayed in text mode with the real states.

The caller asked what the agent could do. Noise turns produced a clarifying question out of nowhere; the
model sent the capabilities question to the policy state, whose `finish` tool handed the call back to the
router without answering, so the router asked "anything else?" over a question nobody answered; the model then
recited a prompt example ("That's all.") as its reply and a bare "Mm-hmm." ended the call. Synthetic data.
"""

import datetime as dt
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
from livekit.agents.llm import ChatMessage, StopResponse

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent import phrases
from supportagent.agent.base import BaseStateAgent
from supportagent.agent.states import flow
from supportagent.agent.states.closing import goodbye_line
from supportagent.agent.states.policy import LOW_CONFIDENCE_OFFER
from supportagent.agent.states.router import ANYTHING_ELSE, CLARIFY, HOW_CAN_I_HELP, RouterAgent
from supportagent.agent.testing import FakeGateway, ScenarioResult, ScriptedLLM, call, run_text_scenario, say
from supportagent.compliance.disclosures import load_disclosures
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.gateway import PolicySource, SearchPolicyOutput, ToolResult
from supportagent.contracts.outcome import Campaign, Direction

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_disclosures(ROOT / "data" / "config" / "disclosures.yaml")
VARS = {"bank_name": "Meridian Bank", "agent_name": "Ava"}
FRANK = PersonaCard(
    customer_id=9001,
    full_name="Frank Alvarez",
    phone="+15550100001",
    extension="1001",
    dob=dt.date(1980, 1, 2),
    loan_id=1,
    loan_last4="4417",
    product_code="PERSONAL",
    situation="synthetic test persona",
    expected_outcome="n/a",
)
FEES = PolicySource(
    doc_id="fees-schedule",
    version="2.1",
    section="Personal loan fees",
    text="The late fee on a personal loan is a flat $25.",
    score=0.74,
)


def _lines(result: ScenarioResult) -> list[str]:
    return [t.text for t in result.transcript if t.role == "agent"]


async def _run(llm: ScriptedLLM, turns: list[str], *, low_confidence: bool = False) -> ScenarioResult:
    async def on_start(state) -> None:  # type: ignore[no-untyped-def]
        state.disclosures = REGISTRY

    chunks = [] if low_confidence else [FEES]
    return await run_text_scenario(
        FRANK,
        turns,
        agent=RouterAgent(variables=VARS),
        llm=llm,
        gateway=FakeGateway(
            FRANK,
            results={
                "search_policy": ToolResult.success(
                    SearchPolicyOutput(chunks=chunks, low_confidence=low_confidence)
                )
            },
        ),
        direction=Direction.inbound,
        campaign=Campaign.inbound,
        on_start=on_start,
    )


# ------------------------------------------------------------------ the phrase detectors


@pytest.mark.parametrize("text", ["", "   ", "\n", "...", " - ", "?!"])
def test_a_turn_with_no_words_is_empty(text: str) -> None:
    assert phrases.is_empty_turn(text) is True


@pytest.mark.parametrize("text", ["I think we are finished here then", "nothing else, all good"])
def test_a_loose_done_statement_lets_finish_call_close(text: str) -> None:
    assert phrases.says_done_loosely(text) is True


@pytest.mark.parametrize("text", ["Mm-hmm.", "Okay, I see.", "Let me think about my payments"])
def test_filler_does_not_let_finish_call_close(text: str) -> None:
    assert phrases.says_done_loosely(text) is False
    assert phrases.is_question_like("Mm-hmm.") is False


@pytest.mark.parametrize("text", ["Hi", "no", "42", "What is the late fee?"])
def test_a_turn_with_words_is_not_empty(text: str) -> None:
    assert phrases.is_empty_turn(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "I just want to know what you learned from me and your capabilities.",
        "What can you do?",
        "what can you do for me",
        "What are your capabilities?",
        "Who are you?",
        "How can you help me?",
        "what do you know",
        "Help me",
        "what are you able to do",
    ],
)
def test_capabilities_questions_are_recognised(text: str) -> None:
    assert phrases.is_capabilities_question(text) is True
    assert phrases.has_router_intent(text) is True  # so the router never asks "I didn't catch that"


@pytest.mark.parametrize(
    "text",
    [
        "What do you know about late fees on personal loans?",
        "What can I do if I miss a payment?",
        "What can you do about my late fee on my loan?",
        "How much do I owe?",
        "Mm-hmm.",
        "",
    ],
)
def test_other_turns_are_not_capabilities_questions(text: str) -> None:
    assert phrases.is_capabilities_question(text) is False


# ------------------------------------------------------------------ 1. an empty turn is ignored


async def test_an_empty_turn_gets_no_reply_and_does_not_count_as_unclear() -> None:
    llm = ScriptedLLM([])
    result = await _run(llm, ["   ", "..."])
    assert _lines(result) == [HOW_CAN_I_HELP]  # no clarifier for noise
    assert result.state.unclear_turns == 0
    assert result.llm.requests == []  # the model was not asked either


async def test_an_empty_turn_is_ignored_by_the_state_screen() -> None:
    agent = RouterAgent(variables=VARS)
    state = _bare_state()
    msg = ChatMessage(role="user", content=["  "], id="m-empty")
    assert await _screen_without_session(agent, state, msg) is True
    assert state.unclear_turns == 0
    assert state.last_caller_text == ""  # an empty turn never replaces the last real one


def _bare_state():  # type: ignore[no-untyped-def]
    from supportagent.agent.testing import _call_state

    return _call_state(FRANK, FakeGateway(FRANK), Direction.inbound, Campaign.inbound)


async def _screen_without_session(agent, state, message) -> bool:  # type: ignore[no-untyped-def]
    with patch.object(BaseStateAgent, "state", new_callable=PropertyMock, return_value=state):
        return await BaseStateAgent._screen(agent, message)


# ------------------------------------------------------------------ 2. what can you do?


async def test_a_capabilities_question_is_answered_in_code_with_an_honest_line() -> None:
    llm = ScriptedLLM([])
    result = await _run(llm, ["I just want to know what you learned from me and your capabilities."])
    lines = _lines(result)
    assert lines[0] == HOW_CAN_I_HELP
    assert lines[1] == flow.CAPABILITIES_LINE
    assert result.llm.requests == []  # the model was not asked
    assert result.states == ["router"]
    assert "$" not in lines[1] and not any(ch.isdigit() for ch in lines[1])  # no figures
    assert "transfer" not in lines[1].lower()  # escalation is not built: not offered
    assert CLARIFY not in lines


# --------------------------------------------------------------- 3. an unanswerable question is never dropped


async def test_policy_finish_on_an_unanswered_question_offers_the_callback_not_a_hand_back() -> None:
    llm = ScriptedLLM(
        [
            call("route_policy_question"),
            call("finish"),  # the 8B model's choice for a question it could not place
        ]
    )
    result = await _run(llm, ["What is the late fee on a spaceship loan?"])
    lines = _lines(result)
    assert result.states == ["router", "policy"]  # not handed back to the router with "anything else?"
    assert LOW_CONFIDENCE_OFFER in lines
    assert ANYTHING_ELSE not in lines
    assert [c for c in result.gateway.calls if c.tool == "request_callback"] == []  # offered, not booked yet


async def test_after_the_offer_a_decline_hands_back_to_the_router() -> None:
    llm = ScriptedLLM(
        [
            call("route_policy_question"),
            call("finish"),  # unanswered: the fixed offer
            call("finish"),  # the caller declined the offer: now it may hand back
        ]
    )
    result = await _run(llm, ["What is the late fee on a spaceship loan?", "No, I don't want that."])
    lines = _lines(result)
    assert lines.index(LOW_CONFIDENCE_OFFER) < lines.index(ANYTHING_ELSE)
    assert result.states == ["router", "policy", "router"]


async def test_after_an_answered_policy_question_finish_hands_back_to_the_router() -> None:
    llm = ScriptedLLM(
        [
            call("route_policy_question"),
            call("search_policy", query="late fees"),
            say("The late fee on a personal loan is a flat twenty-five dollars."),
            call("finish"),
        ]
    )
    result = await _run(llm, ["What is the late fee on a personal loan?", "Okay, I see."])
    assert result.states[:3] == ["router", "policy", "router"]
    assert ANYTHING_ELSE in _lines(result)


# ------------------------------------------------------------------ 4. finish_call is guarded in code


async def test_mm_hmm_after_anything_else_does_not_end_the_call() -> None:
    """The real call's hang-up: "Mm-hmm." followed by the model calling finish_call."""
    llm = ScriptedLLM(
        [
            call("route_policy_question"),
            call("search_policy", query="late fees"),
            say("The late fee on a personal loan is a flat twenty-five dollars."),
            call("finish"),  # the caller is done with policy; back to the router: "anything else?"
        ]
    )
    result = await _run(llm, ["What is the late fee?", "Okay, I see.", "Mm-hmm."])
    assert "closing" not in result.states
    assert result.state.end_reason is None
    assert _lines(result)[-1] == CLARIFY  # asked in code; the model never got to call finish_call
    assert result.llm.remaining == 0


async def test_finish_call_is_refused_unless_the_caller_just_said_they_are_done() -> None:
    """A turn with an intent that is not a farewell: the model's `finish_call` does not close the call."""
    llm = ScriptedLLM(
        [
            call("route_policy_question"),
            call("search_policy", query="late fees"),
            say("The late fee on a personal loan is a flat twenty-five dollars."),
            call("finish"),
            call("finish_call"),  # the model hangs up on "let me think about my payments"
            say("Take your time. What else can I help you with?"),
        ]
    )
    result = await _run(
        llm, ["What is the late fee?", "Okay, I see.", "Let me think about my payments for a moment."]
    )
    assert "closing" not in result.states
    assert result.state.end_reason is None
    finish = [c for c in result.tool_calls if c.name == "finish_call"]
    assert finish and flow.NOT_DONE in (finish[0].output or "")
    assert "Take your time. What else can I help you with?" in _lines(result)


async def test_no_thanks_after_anything_else_ends_the_call() -> None:
    llm = ScriptedLLM(
        [
            call("route_policy_question"),
            call("search_policy", query="late fees"),
            say("The late fee on a personal loan is a flat twenty-five dollars."),
            call("finish"),
        ]
    )
    result = await _run(llm, ["What is the late fee?", "Okay, I see.", "No thanks."])
    assert result.states[-1] == "closing"
    assert _lines(result)[-1] == goodbye_line("Meridian Bank")
    assert result.state.end_reason == flow.END_BY_CALLER


async def test_the_router_never_speaks_a_recited_prompt_example() -> None:
    """The real call: the model answered "That's all." (twice) because the prompt quoted it."""
    llm = ScriptedLLM([say("That's all.")])
    result = await _run(llm, ["I want more about the bank, please."])
    lines = _lines(result)
    assert "That's all." not in lines
    assert CLARIFY in lines  # the caller still hears something useful


def test_the_router_prompt_quotes_no_phrase_a_model_could_recite() -> None:
    text = (ROOT / "src" / "supportagent" / "agent" / "prompts" / "router.md").read_text().lower()
    for fragment in ("that's all", "no thanks", "is that about your own account"):
        assert fragment not in text
    assert "version: '3'" in text


async def test_a_stop_response_is_how_an_ignored_turn_ends() -> None:
    """`on_user_turn_completed` turns a handled screen into a skipped model reply."""
    agent = RouterAgent(variables=VARS)
    state = _bare_state()
    message = ChatMessage(role="user", content=[" "], id="m-1")
    with (
        patch.object(BaseStateAgent, "state", new_callable=PropertyMock, return_value=state),
        pytest.raises(StopResponse),
    ):
        await agent.on_user_turn_completed(None, message)  # type: ignore[arg-type]
