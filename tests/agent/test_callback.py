"""T-AGENT-12 · AGENT-R10: the callback flow with context (U5) in text mode, scripted LLM and fake gateway.

A callback call is outbound, `campaign=callback`. Once the caller is verified the state reads
`get_interaction_history` and `get_loan_status` in code and opens with the context: the figures come from
those two tool results, never from the model. A broken promise leads to a new-promise flow (the gateway
decides), and a callback with nothing outstanding closes politely with no figure.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent.base import check_registered_states, class_tool_names
from supportagent.agent.context import CallState
from supportagent.agent.grounding import assert_grounded
from supportagent.agent.prompts.registry import DEFAULT_PROMPTS_DIR, PromptRegistry
from supportagent.agent.states import flow
from supportagent.agent.states.callback import (
    CALLBACK_FAILED,
    NOT_VERIFIED_LINE,
    NOTHING_OPEN_LINE,
    UNAVAILABLE_LINE,
    CallbackAgent,
    broken_promise,
    context_line,
)
from supportagent.agent.states.greeting import GreetingAgent
from supportagent.agent.testing import FakeGateway, ScenarioResult, ScriptedLLM, call, run_text_scenario, say
from supportagent.common.clock import freeze_clock
from supportagent.compliance.disclosures import DisclosureRegistry, load_disclosures
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.gateway import (
    InteractionHistoryOutput,
    LoanStatusOutput,
    LoanSummary,
    LookupByPhoneOutput,
    PromiseOut,
    RecordPromiseOutput,
    TaskCreatedOutput,
    ToolResult,
)
from supportagent.contracts.outcome import Campaign, Direction, Intent

ROOT = Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 11, 9, 9, 0, tzinfo=dt.UTC)
TODAY = NOW.date()
CALLBACK_AT = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.UTC)
VARIABLES = {"bank_name": "Northwind Bank", "agent_name": "Ava"}
WINDOW_END = TODAY + dt.timedelta(days=14)

FRANK = PersonaCard(
    customer_id=9003,
    full_name="Frank Mensah",
    phone="+15550100003",
    extension="1003",
    dob=dt.date(1984, 3, 14),
    loan_id=3,
    loan_last4="4417",
    product_code="PERSONAL",
    situation="synthetic test persona",
    expected_outcome="n/a",
)


def _loan(overdue: str, since: dt.date | None) -> LoanSummary:
    return LoanSummary.model_validate(
        {
            "loan_masked": "****4417",
            "product_code": "PERSONAL",
            "product_name": "Personal Loan",
            "outstanding_balance": "4100.00",
            "overdue_amount": overdue,
            "oldest_unpaid_due_date": since,
            "next_due_date": dt.date(2026, 12, 4),
            "next_due_amount": "200.72",
            "days_past_due": 5 if since else 0,
            "fees_accrued": "0.00",
            "status": "active",
            "speakable": "overdue",
        }
    )


OVERDUE_LOAN = _loan("200.72", dt.date(2026, 11, 4))
PAID_UP_LOAN = _loan("0.00", None)


def _status(*loans: LoanSummary) -> ToolResult[LoanStatusOutput]:
    return ToolResult.success(LoanStatusOutput(loans=list(loans)))


def _history(*promises: PromiseOut) -> ToolResult[InteractionHistoryOutput]:
    return ToolResult.success(
        InteractionHistoryOutput(recent_calls=[], open_promises=list(promises), open_tasks=[])
    )


BROKEN = PromiseOut(promised_date=dt.date(2026, 11, 6), amount=Decimal("150.00"), status="broken")
OPEN = PromiseOut(promised_date=dt.date(2026, 11, 12), amount=Decimal("150.00"), status="open")
TASK = ToolResult[TaskCreatedOutput].success(
    TaskCreatedOutput.model_validate({"task_id": 7, "type": "callback", "due_at": CALLBACK_AT.isoformat()})
)


def _promise(accepted: bool, reason: str, amount_due: str = "200.72") -> Any:
    out = RecordPromiseOutput.model_validate(
        {
            "accepted": accepted,
            "reason": reason,
            "promise_id": 5 if accepted else None,
            "max_date": WINDOW_END,
            "amount_due": amount_due,
        }
    )
    return ToolResult.success(out)


def _window_rules(promise_date: dt.date, amount: Decimal, loan_last4: str | None = None) -> Any:
    if promise_date > WINDOW_END:
        return _promise(False, "date_out_of_window")
    return _promise(True, "ok")


@pytest.fixture
def prompts() -> PromptRegistry:
    return PromptRegistry(DEFAULT_PROMPTS_DIR)


@pytest.fixture
def registry() -> DisclosureRegistry:
    return load_disclosures(ROOT / "data" / "config" / "disclosures.yaml")


def _starter(registry: DisclosureRegistry, *, verified: bool = True) -> Any:
    async def start(state: CallState) -> None:
        state.disclosures = registry
        state.verified = verified
        state.first_name = "Frank"
        state.states_visited.append("verify" if verified else "greeting")

    return start


async def _run(
    prompts: PromptRegistry,
    registry: DisclosureRegistry,
    steps: list[object],
    results: dict[str, object],
    *,
    turns: list[str] | None = None,
    verified: bool = True,
) -> ScenarioResult:
    with freeze_clock(NOW):
        return await run_text_scenario(
            FRANK,
            turns or [],
            agent=CallbackAgent(prompts=prompts, variables=VARIABLES),
            llm=ScriptedLLM(steps),  # type: ignore[arg-type]
            gateway=FakeGateway(FRANK, results=results),  # type: ignore[arg-type]
            direction=Direction.outbound,
            campaign=Campaign.callback,
            on_start=_starter(registry, verified=verified),
        )


def _spoken(result: ScenarioResult) -> list[str]:
    return [line.text for line in result.transcript if line.role == "agent"]


def _outputs(result: ScenarioResult) -> list[str]:
    return [c.output or "" for c in result.tool_calls]


# --------------------------------------------------------------------------- the context line (pure)


def test_the_context_line_states_the_broken_promise_from_the_history() -> None:
    line = context_line(BROKEN)
    assert "$150.00" in line and "6 November 2026" in line
    assert "haven't seen" in line
    assert_grounded(line, [BROKEN.model_dump_json(), "$150.00", "6 November 2026"])


def test_only_a_broken_promise_gives_a_context_line() -> None:
    assert broken_promise([OPEN]) is None
    assert broken_promise([]) is None
    assert broken_promise([OPEN, BROKEN]) is BROKEN


def test_the_latest_broken_promise_is_the_subject() -> None:
    older = PromiseOut(promised_date=dt.date(2026, 10, 1), amount=Decimal("99.00"), status="broken")
    assert broken_promise([older, BROKEN]) is BROKEN


def test_the_state_registers_with_its_allow_list() -> None:
    check_registered_states()
    assert sorted(class_tool_names(CallbackAgent)) == [
        "finish",
        "offer_callback",
        "record_promise_to_pay",
        "refer_to_human",
        "report_concern",  # shared by every state
    ]
    assert flow.post_verification_state(Direction.outbound, Campaign.callback) == flow.CALLBACK


# --------------------------------------------------------------------------- Frank: a broken promise


async def test_frank_hears_the_broken_promise_then_makes_a_new_one(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    new_day = TODAY + dt.timedelta(days=3)
    result = await _run(
        prompts,
        registry,
        [
            call("record_promise_to_pay", promise_date=new_day.isoformat()),
            say("Thank you, Frank."),
            say("Is there anything else I can help you with?"),
        ],
        {
            "get_interaction_history": _history(BROKEN),
            "get_loan_status": _status(OVERDUE_LOAN),
            "record_promise_to_pay": _window_rules,
        },
        turns=["I can pay on the twelfth."],
    )
    spoken = _spoken(result)
    assert result.gateway.tools_called[:2] == ["get_interaction_history", "get_loan_status"]
    context_at = next(i for i, line in enumerate(spoken) if "$150.00" in line and "6 November 2026" in line)
    ask_at = next(i for i, line in enumerate(spoken) if "$200.72" in line and "date" in line)
    collections_at = next(i for i, line in enumerate(spoken) if line == registry.render("collections").text)
    assert collections_at < context_at < ask_at  # the disclosure comes before any figure
    assert "collections@1.0" in result.outcome["disclosures_given"]
    sent = next(c for c in result.gateway.calls if c.tool == "record_promise_to_pay")
    assert sent.arguments["amount"] == Decimal("200.72")  # the code's amount (loan status), not the model's
    assert sent.arguments["loan_last4"] == "4417"
    promise = result.state.outcome.promise
    assert promise is not None and promise.date == new_day and promise.amount == Decimal("200.72")
    assert Intent.promise_to_pay.value in result.outcome["intents"]
    assert result.states == ["verify", "callback", "closing"]
    for line in spoken[:3]:
        assert_grounded(
            line,
            [
                BROKEN.model_dump_json(),
                OVERDUE_LOAN.model_dump_json(),
                "$150.00",
                "$200.72",
                "6 November 2026",
                "4 November 2026",
                "4417",
                registry.render("collections").text,
            ],
        )


async def test_a_new_promise_beyond_the_window_is_rejected_by_the_gateway_not_the_model(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    too_late = TODAY + dt.timedelta(days=20)
    result = await _run(
        prompts,
        registry,
        [
            call("record_promise_to_pay", promise_date=too_late.isoformat()),
            say("The latest date I can note is 23 November 2026."),
            call("record_promise_to_pay", promise_date=WINDOW_END.isoformat()),
            say("Thank you."),
            say("Anything else?"),
        ],
        {
            "get_interaction_history": _history(BROKEN),
            "get_loan_status": _status(OVERDUE_LOAN),
            "record_promise_to_pay": _window_rules,
        },
        turns=["The twenty-ninth.", "The twenty-third then."],
    )
    first = _outputs(result)[0]
    assert "23 November 2026" in first and "2026-11-23" in first  # the gateway's max_date, rendered in code
    promise = result.state.outcome.promise
    assert promise is not None and promise.date == WINDOW_END  # the rejected date was never recorded


async def test_the_gateway_failing_a_promise_promises_nothing(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        [
            call("record_promise_to_pay", promise_date=(TODAY + dt.timedelta(days=2)).isoformat()),
            say("I'm sorry, I couldn't record that."),
        ],
        {
            "get_interaction_history": _history(BROKEN),
            "get_loan_status": _status(OVERDUE_LOAN),
            "record_promise_to_pay": ToolResult.failure("upstream_error", "down"),
        },
        turns=["Thursday."],
    )
    assert "could not be recorded" in _outputs(result)[0]
    assert result.state.outcome.promise is None


# --------------------------------------------------------------------------- nothing to talk about


async def test_a_callback_with_nothing_outstanding_closes_politely_with_no_figure(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        [say("Is there anything else I can help you with?")],
        {"get_interaction_history": _history(), "get_loan_status": _status(PAID_UP_LOAN)},
    )
    spoken = _spoken(result)
    assert spoken[0] == NOTHING_OPEN_LINE
    assert not any("$" in line for line in spoken)
    assert "collections@1.0" not in result.outcome["disclosures_given"]  # nothing was collected on
    assert result.states == ["verify", "callback", "closing"]
    assert result.state.outcome.promise is None


async def test_a_broken_promise_that_is_paid_up_now_is_not_chased(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        [say("Is there anything else?")],
        {"get_interaction_history": _history(BROKEN), "get_loan_status": _status(PAID_UP_LOAN)},
    )
    assert _spoken(result)[0] == NOTHING_OPEN_LINE
    assert not any("150.00" in line for line in _spoken(result))


async def test_an_open_balance_with_no_broken_promise_gets_the_balance_and_a_promise_flow(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        [call("offer_callback"), say("A colleague will call you back.")],
        {
            "get_interaction_history": _history(OPEN),
            "get_loan_status": _status(OVERDUE_LOAN),
            "request_callback": TASK,
        },
        turns=["Actually, please have someone call me."],
    )
    spoken = _spoken(result)
    assert any("$200.72" in line and "4 November 2026" in line for line in spoken)
    assert not any("150.00" in line for line in spoken)  # an open promise is not a broken one
    assert result.state.outcome.callback is not None


# --------------------------------------------------------------------------- guardrails


async def test_an_unverified_caller_hears_no_figure_and_goes_to_verification(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        [],
        {"get_interaction_history": _history(BROKEN), "get_loan_status": _status(OVERDUE_LOAN)},
        verified=False,
    )
    assert _spoken(result)[0] == NOT_VERIFIED_LINE
    assert result.gateway.tools_called == []  # not even read: no data before verification
    assert not any("$" in line for line in _spoken(result))
    assert result.states[-1] == "verify"


async def test_a_failed_read_says_no_figure_and_closes(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        [say("Is there anything else I can help you with?")],
        {
            "get_interaction_history": ToolResult.failure("upstream_error", "down"),
            "get_loan_status": _status(OVERDUE_LOAN),
        },
    )
    assert _spoken(result)[0] == UNAVAILABLE_LINE
    assert not any("$" in line for line in _spoken(result))
    assert result.states[-1] == "closing"


async def test_a_callback_the_gateway_refuses_is_not_promised(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        [call("offer_callback"), say("I'm sorry, I couldn't arrange that.")],
        {
            "get_interaction_history": _history(BROKEN),
            "get_loan_status": _status(OVERDUE_LOAN),
            "request_callback": ToolResult.failure("upstream_error", "down"),
        },
        turns=["Please just call me back."],
    )
    assert CALLBACK_FAILED in _outputs(result)[0]
    assert result.state.outcome.callback is None


async def test_a_hardship_the_caller_states_goes_to_the_honest_referral(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    """Escalation (T-AGENT-11) is not built: the referral books a callback and speaks the gateway's time."""
    result = await _run(
        prompts,
        registry,
        [say("Is there anything else?")],
        {
            "get_interaction_history": _history(BROKEN),
            "get_loan_status": _status(OVERDUE_LOAN),
            "request_callback": TASK,
        },
        turns=["I lost my job last month and I can't pay."],
    )
    assert result.gateway.tools_called.count("request_callback") == 1
    assert result.state.outcome.callback is not None
    assert result.states[-1] == "closing"
    assert result.state.outcome.promise is None


# --------------------------------------------------------------------------- the whole callback path


async def test_a_callback_call_goes_greeting_verify_callback(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    async def start(state: CallState) -> None:
        state.disclosures = registry

    with freeze_clock(NOW):
        result = await run_text_scenario(
            FRANK,
            ["Yes, this is Frank.", "Frank Mensah, March 14th 1984, 4417"],
            agent=GreetingAgent(prompts=prompts, variables=VARIABLES),
            llm=ScriptedLLM(
                [
                    call("confirm_right_party"),
                    call("verify_identity", full_name="Frank Mensah", dob="1984-03-14", loan_last4="4417"),
                    say("What date can you manage?"),
                ]
            ),
            gateway=FakeGateway(
                FRANK,
                {
                    "lookup_by_phone": ToolResult.success(
                        LookupByPhoneOutput(match=True, first_name="Frank")
                    ),
                    "get_interaction_history": _history(BROKEN),
                    "get_loan_status": _status(OVERDUE_LOAN),
                },
            ),
            direction=Direction.outbound,
            campaign=Campaign.callback,
            on_start=start,
        )
    assert result.states == ["greeting", "verify", "callback"]
    spoken = _spoken(result)
    assert any("6 November 2026" in line for line in spoken)  # the context line
    assert result.outcome["disclosures_given"] == ["ai_recording@1.0", "collections@1.0"]
