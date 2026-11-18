"""T-AGENT-09 · AGENT-R7: the pre-due reminder flow (U1) in text mode, scripted LLM and fake gateway.

Olivia (due in two days, never late) is verified and the state reads her loan in code: the amount and the
due date are rendered from the tool result, never by the model. Every figure spoken must appear in a tool
result of the same call (`assert_grounded`). A reminder is not collections: no collections disclosure is
ever spoken.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent.base import check_registered_states, class_tool_names
from supportagent.agent.context import CallState
from supportagent.agent.grounding import assert_grounded
from supportagent.agent.prompts.registry import DEFAULT_PROMPTS_DIR, PromptRegistry
from supportagent.agent.states import flow
from supportagent.agent.states.greeting import GreetingAgent
from supportagent.agent.states.reminder import (
    CALLBACK_FAILED,
    CHANNELS_QUERY,
    CONSEQUENCES_QUERY,
    ReminderAgent,
    reminder_line,
)
from supportagent.agent.testing import FakeGateway, ScenarioResult, ScriptedLLM, call, run_text_scenario, say
from supportagent.common.clock import freeze_clock
from supportagent.compliance.disclosures import load_disclosures
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.gateway import (
    LoanStatusOutput,
    LoanSummary,
    LookupByPhoneOutput,
    PolicySource,
    SearchPolicyOutput,
    TaskCreatedOutput,
    ToolResult,
)
from supportagent.contracts.outcome import Campaign, Direction, Intent

OLIVIA = PersonaCard(
    customer_id=9001,
    full_name="Olivia Bennett",
    phone="+15550100001",
    extension="1001",
    dob=dt.date(1990, 7, 22),
    loan_id=1,
    loan_last4="2861",
    product_code="PERSONAL",
    situation="synthetic test persona",
    expected_outcome="n/a",
)
ROOT = Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 11, 9, 9, 0, tzinfo=dt.UTC)
CALLBACK_AT = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.UTC)


def _loan(last4: str, due: dt.date | None, amount: str | None, product: str = "Personal Loan") -> LoanSummary:
    return LoanSummary(
        loan_masked=f"****{last4}",
        product_code="PERSONAL",
        product_name=product,
        outstanding_balance=Decimal("4100.00"),
        overdue_amount=Decimal("0.00"),
        oldest_unpaid_due_date=None,
        next_due_date=due,
        next_due_amount=Decimal(amount) if amount else None,
        days_past_due=0,
        fees_accrued=Decimal("0.00"),
        status="active",
        speakable="nothing overdue",
    )


PERSONAL = _loan("2861", dt.date(2026, 11, 11), "245.10")
AUTO = _loan("3310", dt.date(2026, 11, 25), "415.30", "Auto Loan")


def _status(*loans: LoanSummary) -> ToolResult[LoanStatusOutput]:
    return ToolResult.success(LoanStatusOutput(loans=list(loans)))


def _policy(text: str, section: str = "Paying") -> ToolResult[SearchPolicyOutput]:
    chunk = PolicySource(doc_id="repayment-channels", version="1.0", section=section, text=text, score=0.9)
    return ToolResult.success(SearchPolicyOutput(chunks=[chunk], low_confidence=False))


def _by_query(channels: ToolResult[SearchPolicyOutput], consequences: ToolResult[SearchPolicyOutput]):  # type: ignore[no-untyped-def]
    def respond(query: str, product: object = None) -> ToolResult[SearchPolicyOutput]:
        return consequences if query == CONSEQUENCES_QUERY else channels

    return respond


CHANNELS = _policy("You can pay in the mobile app, at a branch, or by standing order.")
CONSEQUENCES = _policy(
    "A late fee of $25 applies once an instalment is unpaid after the 15 day grace period.", "Late fees"
)
TASK = ToolResult[TaskCreatedOutput].success(
    TaskCreatedOutput.model_validate({"task_id": 7, "type": "callback", "due_at": CALLBACK_AT.isoformat()})
)


@pytest.fixture
def prompts() -> PromptRegistry:
    return PromptRegistry(DEFAULT_PROMPTS_DIR)


async def _verified(state: CallState) -> None:
    state.verified = True
    state.first_name = "Olivia"
    state.states_visited.append("verify")


async def _unverified(state: CallState) -> None:
    state.states_visited.append("greeting")


async def _run(
    prompts: PromptRegistry,
    steps: list[object],
    results: dict[str, object],
    *,
    turns: list[str] | None = None,
    verified: bool = True,
) -> ScenarioResult:
    with freeze_clock(NOW):
        return await run_text_scenario(
            OLIVIA,
            turns or [],
            agent=ReminderAgent(
                prompts=prompts, variables={"bank_name": "Northwind Bank", "agent_name": "Ava"}
            ),
            llm=ScriptedLLM(steps),  # type: ignore[arg-type]
            gateway=FakeGateway(OLIVIA, results=results),  # type: ignore[arg-type]
            direction=Direction.outbound,
            campaign=Campaign.pre_due,
            on_start=_verified if verified else _unverified,
        )


def _spoken(result: ScenarioResult) -> list[str]:
    return [line.text for line in result.transcript if line.role == "agent"]


def _outputs(result: ScenarioResult) -> list[str]:
    return [c.output or "" for c in result.tool_calls]


# --------------------------------------------------------------------------- the reminder line (pure)


def test_the_line_states_the_amount_and_date_from_the_tool_result() -> None:
    line = reminder_line(PERSONAL)
    assert "$245.10" in line and "11 November 2026" in line
    assert "Personal Loan ending 2861" in line
    assert_grounded(line, [PERSONAL.model_dump_json(), "$245.10", "11 November 2026", "2861"])


def test_the_state_registers_with_its_allow_list() -> None:
    check_registered_states()
    assert sorted(class_tool_names(ReminderAgent)) == [
        "finish",
        "offer_callback",
        "record_intent_to_pay",
        "report_concern",  # shared by every state
        "search_policy",
    ]
    assert flow.post_verification_state(Direction.outbound, Campaign.pre_due) == flow.REMINDER


# --------------------------------------------------------------------------- Olivia, happy path


async def test_olivia_hears_the_reminder_and_confirms_intent_to_pay(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        [
            say("You can pay in the mobile app, at a branch or by standing order. Can you pay by then?"),
            call("record_intent_to_pay", will_pay_on_time=True),
            say("Thank you, I've noted that."),
            say("Is there anything else I can help you with?"),
        ],
        {"get_loan_status": _status(PERSONAL), "search_policy": CHANNELS},
        turns=["Yes, I'll pay on the eleventh."],
    )
    spoken = _spoken(result)
    assert "$245.10" in spoken[0] and "11 November 2026" in spoken[0]  # said by the code, first
    assert result.states == ["verify", "reminder", "closing"]
    assert result.outcome["intents"] == [Intent.intent_to_pay.value]
    assert result.gateway.tools_called == ["get_loan_status", "search_policy"]
    assert result.outcome["kb_sources"] == ["repayment-channels@1.0#Paying"]
    assert not any("collection" in line.lower() for line in spoken)  # no collections disclosure
    assert "collections_disclosure" not in str(result.outcome["disclosures_given"])
    assert result.llm.remaining == 0
    assert_grounded(" ".join(spoken[:1]), [PERSONAL.model_dump_json(), "$245.10", "11 November 2026", "2861"])


async def test_the_model_cannot_talk_before_the_code_has_read_the_loan(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        [say("Can you pay by then?")],
        {"get_loan_status": _status(PERSONAL), "search_policy": CHANNELS},
    )
    assert result.gateway.tools_called[0] == "get_loan_status"
    assert "245.10" in _spoken(result)[0]


# --------------------------------------------------------------------------- paying late


async def test_a_caller_who_will_pay_late_hears_the_policy_consequences_and_is_offered_a_callback(
    prompts: PromptRegistry,
) -> None:
    result = await _run(
        prompts,
        [
            say("Can you pay by then?"),
            call("record_intent_to_pay", will_pay_on_time=False),
            say("A late fee of $25 applies after the 15 day grace period. Would you like a call back?"),
            call("offer_callback"),
            say("A colleague will call you back."),
        ],
        {
            "get_loan_status": _status(PERSONAL),
            "search_policy": _by_query(CHANNELS, CONSEQUENCES),
            "request_callback": TASK,
        },
        turns=["I'll be a few days late, payday is the 14th.", "Yes please, call me back."],
    )
    queries = [c.arguments["query"] for c in result.gateway.calls if c.tool == "search_policy"]
    assert queries == [CHANNELS_QUERY, CONSEQUENCES_QUERY]
    assert "$25" in _outputs(result)[0]  # the consequences are the retrieved text
    assert Intent.intent_to_pay.value not in result.outcome["intents"]  # a late payment is not intent to pay
    assert Intent.callback_request.value in result.outcome["intents"]
    assert result.gateway.tools_called.count("request_callback") == 1
    assert result.state.outcome.callback is not None
    assert "repayment-channels@1.0#Late fees" in result.outcome["kb_sources"]
    assert result.states == ["verify", "reminder"]


async def test_a_callback_the_gateway_refuses_is_not_promised(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        [
            say("Can you pay by then?"),
            call("offer_callback"),
            say("I'm sorry, I couldn't arrange that."),
        ],
        {
            "get_loan_status": _status(PERSONAL),
            "search_policy": CHANNELS,
            "request_callback": ToolResult.failure("upstream_error", "down"),
        },
        turns=["Please call me back."],
    )
    assert CALLBACK_FAILED in _outputs(result)[0]
    assert result.state.outcome.callback is None


# --------------------------------------------------------------------------- the code decides, not the model


async def test_with_two_loans_the_reminder_is_for_the_one_due_first(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        [say("Can you pay by then?")],
        {"get_loan_status": _status(AUTO, PERSONAL), "search_policy": CHANNELS},
    )
    first = _spoken(result)[0]
    assert "$245.10" in first and "2861" in first
    assert "415.30" not in first  # the other loan's figure is never said


async def test_a_loan_with_nothing_due_is_not_reminded_and_the_call_closes(prompts: PromptRegistry) -> None:
    nothing = _loan("2861", None, None)
    result = await _run(
        prompts,
        [say("Is there anything else I can help you with?")],
        {"get_loan_status": _status(nothing)},
    )
    assert result.states == ["verify", "reminder", "closing"]
    assert "$" not in _spoken(result)[0]
    assert Intent.intent_to_pay.value not in result.outcome["intents"]


async def test_when_the_account_system_fails_no_figure_is_said(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        [say("Is there anything else I can help you with?")],
        {"get_loan_status": ToolResult.failure("upstream_error", "down")},
    )
    assert "$" not in " ".join(_spoken(result))
    assert result.states == ["verify", "reminder", "closing"]


async def test_an_unverified_caller_gets_no_figures_and_goes_to_verification(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        [say("Please tell me your full name, date of birth and the last four digits of your loan.")],
        {"get_loan_status": _status(PERSONAL)},
        verified=False,
    )
    assert "get_loan_status" not in result.gateway.tools_called
    assert "$" not in " ".join(_spoken(result)[:1])
    assert result.states[-1] == "verify"


async def test_a_farewell_closes_the_call_without_the_model(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        [say("Can you pay by then?")],
        {"get_loan_status": _status(PERSONAL), "search_policy": CHANNELS},
        turns=["Goodbye."],
    )
    assert result.states[-1] == "closing"
    assert result.state.end_reason == flow.END_BY_CALLER


# --------------------------------------------------------------------------- the whole outbound path


async def test_a_pre_due_call_goes_greeting_verify_reminder_with_no_collections_disclosure(
    prompts: PromptRegistry,
) -> None:
    """Olivia answers, verifies, and the campaign table sends her to the reminder (never to collections)."""
    registry = load_disclosures(ROOT / "data" / "config" / "disclosures.yaml")
    variables = {"bank_name": "Meridian Bank", "agent_name": "Ava"}

    async def start(state: CallState) -> None:
        state.disclosures = registry

    with freeze_clock(NOW):
        result = await run_text_scenario(
            OLIVIA,
            ["Yes, this is Olivia.", "Olivia Bennett, July 22nd 1990, 2861"],
            agent=GreetingAgent(prompts=prompts, variables=variables),
            llm=ScriptedLLM(
                [
                    call("confirm_right_party"),
                    call("verify_identity", full_name="Olivia Bennett", dob="1990-07-22", loan_last4="2861"),
                    say("Can you pay by then?"),
                ]
            ),
            gateway=FakeGateway(
                OLIVIA,
                {
                    "lookup_by_phone": ToolResult.success(
                        LookupByPhoneOutput(match=True, first_name="Olivia")
                    ),
                    "get_loan_status": _status(PERSONAL),
                    "search_policy": CHANNELS,
                },
            ),
            direction=Direction.outbound,
            campaign=Campaign.pre_due,
            on_start=start,
        )
    assert result.states == ["greeting", "verify", "reminder"]
    assert result.state.verified is True
    assert result.gateway.tools_called == [
        "lookup_by_phone",
        "verify_identity",
        "get_loan_status",
        "search_policy",
    ]
    spoken = " ".join(_spoken(result))
    assert "$245.10" in spoken and "11 November 2026" in spoken
    assert "collections" not in str(result.outcome["disclosures_given"])
