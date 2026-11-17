"""T-AGENT-07 · AGENT-R6: policy questions answered only from `search_policy` chunks, with citations.

Text mode with the scripted LLM. The caller reaches `PolicyAgent` the way a real inbound caller does: through
the router, whose hand-off is silent, so the policy state answers the question already in the chat history.
The scripted LLM ignores `tool_choice`, so `RecordingLLM` notes what each request asked for: that is how the
"no policy answer without a search in the same turn" rule is checked.
"""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from livekit.agents.llm import ChatContext, ChatMessage, FunctionCallOutput

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent.base import class_tool_names
from supportagent.agent.states import flow
from supportagent.agent.states.closing import goodbye_line
from supportagent.agent.states.policy import (
    FOLLOW_UP_DELAY,
    LOW_CONFIDENCE_OFFER,
    UNAVAILABLE_OFFER,
    PolicyAgent,
    render_excerpts,
    tool_choice_for,
    valid_product,
)
from supportagent.agent.states.router import ANYTHING_ELSE, HOW_CAN_I_HELP, RouterAgent
from supportagent.agent.states.verify import ASK_FOR_DETAILS
from supportagent.agent.testing import (
    FakeGateway,
    ScenarioResult,
    ScriptedLLM,
    call,
    run_text_scenario,
    say,
)
from supportagent.common import clock
from supportagent.compliance.disclosures import load_disclosures
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.gateway import PolicySource, SearchPolicyOutput, TaskCreatedOutput, ToolResult
from supportagent.contracts.outcome import Campaign, Direction, Intent

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_disclosures(ROOT / "data" / "config" / "disclosures.yaml")
VARS = {"bank_name": "Meridian Bank", "agent_name": "Ava"}
QUESTION = "Can I pay my loan off early?"

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

EARLY = PolicySource(
    doc_id="early-repayment-payoff",
    version="1.0",
    section="Paying early",
    text="You may repay a personal loan early at any time. There is no early repayment fee.",
    score=0.81,
)
EARLY_FEES = PolicySource(
    doc_id="early-repayment-payoff",
    version="1.0",
    section="Payoff quotes",
    text="Ask for a payoff quote; it is valid for ten days.",
    score=0.7,
)
FEES = PolicySource(
    doc_id="fees-schedule",
    version="2.1",
    section="Personal loan fees",
    text="The late fee on a personal loan is a flat $25.",
    score=0.74,
)


def confident(*chunks: PolicySource) -> ToolResult[SearchPolicyOutput]:
    return ToolResult.success(SearchPolicyOutput(chunks=list(chunks), low_confidence=False))


def unsure(*chunks: PolicySource) -> ToolResult[SearchPolicyOutput]:
    return ToolResult.success(SearchPolicyOutput(chunks=list(chunks), low_confidence=True))


CALLBACK_OK = ToolResult.success(
    TaskCreatedOutput(task_id=7, type="callback", due_at=dt.datetime(2026, 11, 10, 14, 0, tzinfo=dt.UTC))
)


class RecordingLLM(ScriptedLLM):
    """A scripted LLM that also records the `tool_choice` of every request."""

    def __init__(self, steps: list[Any]) -> None:
        super().__init__(steps)
        self.choices: list[Any] = []

    def chat(
        self, *, chat_ctx: ChatContext, tools: Any = None, tool_choice: Any = None, **kwargs: Any
    ) -> Any:
        self.choices.append(tool_choice if isinstance(tool_choice, str) else None)
        return super().chat(chat_ctx=chat_ctx, tools=tools, **kwargs)


async def _run(
    steps: list[Any],
    turns: list[str],
    *,
    results: dict[str, Any] | None = None,
    verified: bool = False,
) -> tuple[ScenarioResult, RecordingLLM]:
    llm = RecordingLLM(steps)

    async def on_start(state: Any) -> None:
        state.disclosures = REGISTRY
        state.verified = verified

    result = await run_text_scenario(
        FRANK,
        turns,
        agent=RouterAgent(variables=VARS),
        llm=llm,  # type: ignore[arg-type]
        gateway=FakeGateway(FRANK, results=results),
        direction=Direction.inbound,
        campaign=Campaign.inbound,
        on_start=on_start,
    )
    return result, llm


def _agent_lines(result: ScenarioResult) -> list[str]:
    return [t.text for t in result.transcript if t.role == "agent"]


def _tools_called(result: ScenarioResult) -> list[str]:
    return list(result.gateway.tools_called)


# --------------------------------------------------------------------------- the answer and its citation


async def test_can_i_pay_early_is_answered_from_the_retrieved_chunk_and_cites_the_doc() -> None:
    result, llm = await _run(
        [
            call("route_policy_question"),
            call("search_policy", query="paying a loan off early"),
            say("Yes, you can pay early, and there is no early repayment fee."),
        ],
        [QUESTION],
        results={"search_policy": confident(EARLY)},
    )

    assert result.states == ["router", "policy"]
    assert _tools_called(result) == ["search_policy"]
    assert result.state.kb_sources == ["early-repayment-payoff@1.0#Paying early"]
    assert result.state.outcome_so_far()["kb_sources"] == ["early-repayment-payoff@1.0#Paying early"]
    # `finalize` writes the outcome from the OutcomeBuilder, so the citation must be there too (T-AGENT-08).
    assert result.state.outcome.kb_sources == ["early-repayment-payoff@1.0#Paying early"]
    assert _agent_lines(result) == [
        HOW_CAN_I_HELP,
        "Yes, you can pay early, and there is no early repayment fee.",
    ]
    assert Intent.policy_question in result.state.intents
    assert llm.remaining == 0


async def test_the_model_sees_the_retrieved_text_and_its_citation() -> None:
    seen: list[str] = []

    def spy(chat: ChatContext) -> Any:
        outputs = [i for i in chat.items if isinstance(i, FunctionCallOutput) and i.name == "search_policy"]
        seen.append(outputs[-1].output)
        return say("Yes, you can pay early.")

    await _run(
        [call("route_policy_question"), call("search_policy", query="early"), spy],
        [QUESTION],
        results={"search_policy": confident(EARLY)},
    )
    assert "early-repayment-payoff@1.0" in seen[0]
    assert "no early repayment fee" in seen[0]


async def test_each_document_is_recorded_once_however_often_it_is_retrieved() -> None:
    result, _ = await _run(
        [
            call("route_policy_question"),
            call("search_policy", query="paying early"),
            say("Yes, you can pay early."),
            call("search_policy", query="paying early again"),
            say("As I said, yes."),
            call("search_policy", query="late fee"),
            say("The late fee is a flat twenty-five dollars."),
        ],
        [QUESTION, "Really?", "And the late fee?"],
        results={
            "search_policy": lambda **kw: (
                confident(FEES) if "late" in kw["query"] else confident(EARLY, EARLY_FEES)
            )
        },
    )
    assert result.state.kb_sources == [
        "early-repayment-payoff@1.0#Paying early",
        "early-repayment-payoff@1.0#Payoff quotes",
        "fees-schedule@2.1#Personal loan fees",
    ]


# --------------------------------------------------------------------------- low confidence


@pytest.mark.parametrize(
    "found",
    [unsure(EARLY), unsure(), confident()],
    ids=["below-threshold-with-chunks", "below-threshold-no-chunks", "no-chunks"],
)
async def test_low_confidence_makes_no_policy_claim_and_offers_a_follow_up(
    found: ToolResult[SearchPolicyOutput],
) -> None:
    result, llm = await _run(
        [call("route_policy_question"), call("search_policy", query="early payoff")],
        [QUESTION],
        results={"search_policy": found},
    )

    assert _agent_lines(result) == [HOW_CAN_I_HELP, LOW_CONFIDENCE_OFFER]
    assert "call back from a colleague" in _agent_lines(result)[-1]  # the follow-up offer
    assert result.state.kb_sources == []  # nothing was said from policy, so nothing is cited
    assert llm.remaining == 0 and len(llm.requests) == 2  # the model is not asked to say anything more
    spoken = " ".join(_agent_lines(result))
    assert "fee" not in spoken.lower() and "early" not in spoken.lower()


async def test_an_unavailable_policy_search_says_so_and_offers_a_follow_up() -> None:
    result, _ = await _run(
        [call("route_policy_question"), call("search_policy", query="early payoff")],
        [QUESTION],
        results={"search_policy": ToolResult.failure("upstream_error", "kb down")},
    )
    assert _agent_lines(result) == [HOW_CAN_I_HELP, UNAVAILABLE_OFFER]
    assert result.state.kb_sources == []


async def test_accepting_the_follow_up_books_a_callback(frozen_clock: Any) -> None:
    result, _ = await _run(
        [
            call("route_policy_question"),
            call("search_policy", query="early payoff"),
            call("offer_follow_up", topic="early payoff"),
            say("Done: a colleague will call you back tomorrow."),
        ],
        [QUESTION, "Yes please, call me back."],
        results={"search_policy": unsure(), "request_callback": CALLBACK_OK},
    )

    booked = [c for c in result.gateway.calls if c.tool == "request_callback"]
    assert len(booked) == 1
    args = booked[0].arguments
    assert args["preferred_at"] == clock.now() + FOLLOW_UP_DELAY
    assert args["preferred_at"].tzinfo is not None
    assert "policy" in args["reason"] and "early payoff" in args["reason"]
    assert Intent.callback_request in result.state.intents
    assert _agent_lines(result)[-1] == "Done: a colleague will call you back tomorrow."


async def test_a_failed_callback_is_not_reported_as_booked(frozen_clock: Any) -> None:
    result, _ = await _run(
        [
            call("route_policy_question"),
            call("search_policy", query="early payoff"),
            call("offer_follow_up", topic="early payoff"),
            say("I could not arrange that just now."),
        ],
        [QUESTION, "Yes please."],
        results={
            "search_policy": unsure(),
            "request_callback": ToolResult.failure("not_found", "no customer"),
        },
    )
    assert Intent.callback_request not in result.state.intents


# --------------------------------------------------------------------------- no answer without a search


async def test_the_first_model_step_of_every_caller_turn_must_call_a_tool() -> None:
    """tool_choice=required on the first step; "none" once the search result is in, so it must speak."""
    _, llm = await _run(
        [
            call("route_policy_question"),
            call("search_policy", query="paying early"),
            say("Yes, you can pay early."),
        ],
        [QUESTION],
        results={"search_policy": confident(EARLY)},
    )
    # request 1: the router (no constraint); 2: policy, first step; 3: policy, after the search result.
    assert llm.choices == [None, "required", "none"]
    assert len(llm.requests) == 3
    assert "search_policy" in llm.requests[1]
    assert {"search_policy", "offer_follow_up", "finish"} <= set(llm.requests[1])


def test_tool_choice_for_is_required_on_a_caller_message_and_none_after_any_tool_result() -> None:
    user = ChatMessage(role="user", content=["Can I pay early?"])
    ok = FunctionCallOutput(call_id="c1", name="search_policy", output="excerpts", is_error=False)
    failed = FunctionCallOutput(call_id="c2", name="search_policy", output="boom", is_error=True)
    assert tool_choice_for([]) == "required"
    assert tool_choice_for([user]) == "required"
    assert tool_choice_for([user, ok]) == "none"
    assert tool_choice_for([user, failed]) == "none"
    assert tool_choice_for([user, ChatMessage(role="assistant", content=["Yes."])]) == "required"


def test_render_excerpts_lists_every_chunk_with_its_citation_and_nothing_else() -> None:
    text = render_excerpts([EARLY, FEES])
    assert (
        "early-repayment-payoff@1.0#Paying early" in text and "fees-schedule@2.1#Personal loan fees" in text
    )
    assert EARLY.text in text and FEES.text in text
    assert text.index(EARLY.text) < text.index(FEES.text)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("PERSONAL", "PERSONAL"),
        ("AUTO", "AUTO"),
        ("personal", None),
        ("YACHT", None),
        ("", None),
        (None, None),
    ],
)
def test_valid_product_passes_only_a_known_product_code(given: str | None, expected: str | None) -> None:
    assert valid_product(given) == expected


async def test_a_bad_product_from_the_model_is_dropped_so_the_gateway_default_applies() -> None:
    result, _ = await _run(
        [
            call("route_policy_question"),
            call("search_policy", query="late fee", product="YACHT"),
            say("The late fee is a flat twenty-five dollars."),
        ],
        ["What is the late fee?"],
        results={"search_policy": confident(FEES)},
    )
    sent = next(c for c in result.gateway.calls if c.tool == "search_policy")
    assert sent.arguments["product"] is None


# --------------------------------------------------------------------------- hand-off and tools


async def test_finishing_returns_to_the_router_which_asks_if_there_is_anything_else() -> None:
    result, _ = await _run(
        [
            call("route_policy_question"),
            call("search_policy", query="paying early"),
            say("Yes, you can pay early."),
            call("finish"),
        ],
        [QUESTION, "Okay, that makes sense."],
        results={"search_policy": confident(EARLY)},
    )
    assert result.states == ["router", "policy", "router"]
    assert _agent_lines(result)[-1] == ANYTHING_ELSE


def test_the_policy_state_has_four_domain_tools_and_no_data_tools() -> None:
    names = set(class_tool_names(PolicyAgent))
    assert names == {"search_policy", "offer_follow_up", "route_to_account", "finish", "report_concern"}
    assert flow.resolve(flow.POLICY) is PolicyAgent


# ------------------------------------------------------------ account questions and goodbyes (in code)

LATE_FEE_ANSWER = "The late fee on a personal loan is a flat twenty-five dollars."


async def test_an_account_question_in_the_policy_state_goes_to_verification_not_the_policy_fallback() -> None:
    """The first real call: "how much do I owe the bank" got "I can't look that up right now", twice."""
    result, llm = await _run(
        [call("route_policy_question"), call("search_policy", query="late fee"), say(LATE_FEE_ANSWER)],
        ["What is the late fee on a personal loan?", "And how much do I owe the bank?"],
        results={"search_policy": confident(FEES)},
    )
    assert result.states == ["router", "policy", "verify"]
    assert _tools_called(result).count("search_policy") == 1  # the second turn never reached policy search
    assert result.state.after_verification == "account"
    assert "account_question" in [str(i) for i in result.state.intents]
    lines = _agent_lines(result)
    assert lines[-1] == ASK_FOR_DETAILS
    assert UNAVAILABLE_OFFER not in lines and LOW_CONFIDENCE_OFFER not in lines
    assert llm.remaining == 0  # no model step was spent on the account question


async def test_a_verified_caller_asking_about_their_account_in_policy_goes_to_the_account_state() -> None:
    result, _ = await _run(
        [
            call("route_policy_question"),
            call("search_policy", query="late fee"),
            say(LATE_FEE_ANSWER),
            say("Your balance is on screen."),  # the account state's first reply
        ],
        ["What is the late fee on a personal loan?", "What's my balance?"],
        results={"search_policy": confident(FEES)},
        verified=True,
    )
    assert result.states == ["router", "policy", "account"]
    assert result.state.after_verification is None


async def test_the_model_can_route_an_account_question_with_the_tool_too() -> None:
    result, _ = await _run(
        [call("route_policy_question"), call("route_to_account")],
        ["Tell me about my payments, I am confused"],  # not caught by the phrase check
    )
    assert result.states == ["router", "policy", "verify"]
    assert result.state.after_verification == "account"


async def test_a_genuine_policy_question_still_goes_through_search_policy() -> None:
    result, llm = await _run(
        [
            call("route_policy_question"),
            call("search_policy", query="late fee"),
            say(LATE_FEE_ANSWER),
            call("search_policy", query="grace period"),
            say("The grace period is fifteen days."),
        ],
        ["What is the late fee on a personal loan?", "What is the grace period on a personal loan?"],
        results={"search_policy": confident(FEES)},
    )
    assert result.states == ["router", "policy"]
    assert _tools_called(result).count("search_policy") == 2
    assert llm.remaining == 0


async def test_a_goodbye_in_the_policy_state_closes_the_call_without_a_model_reply() -> None:
    result, llm = await _run(
        [call("route_policy_question"), call("search_policy", query="late fee"), say(LATE_FEE_ANSWER)],
        ["What is the late fee on a personal loan?", "Okay, bye."],
        results={"search_policy": confident(FEES)},
    )
    assert result.states == ["router", "policy", "closing"]
    assert result.state.end_reason == flow.END_BY_CALLER
    assert _agent_lines(result)[-1] == goodbye_line("Meridian Bank")
    assert llm.remaining == 0
