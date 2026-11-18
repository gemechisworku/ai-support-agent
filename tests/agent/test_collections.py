"""T-AGENT-10 · AGENT-R8, AGENT-R4: the missed-payment collections flow (U2) in text mode.

Scripted LLM, fake gateway, real states. The code decides everything that matters: the collections disclosure
is spoken verbatim before the first amount, every amount and date comes from a tool result, the gateway (not
the model) accepts or rejects a promise, a pending payment is only "in transit" if the code saw one, and a
hardship or a dispute goes to a human path that promises nothing the gateway did not book.

Escalation (T-AGENT-11) is not built yet, so a referral here is the honest fallback: a callback booked through
the gateway and spoken from its answer. The tests that depend on that say so.
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
from supportagent.agent.states.collections import (
    CALLBACK_FAILED,
    NOT_PENDING_NOTE,
    PROMISE_FAILED,
    CollectionsPurposeAgent,
    CollectionsResolveAgent,
    covering_pending,
    overdue_loan,
    purpose_line,
)
from supportagent.agent.states.greeting import GreetingAgent
from supportagent.agent.testing import FakeGateway, ScenarioResult, ScriptedLLM, call, run_text_scenario, say
from supportagent.common.clock import freeze_clock
from supportagent.compliance.disclosures import DisclosureRegistry, load_disclosures
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.gateway import (
    ContactFlagOutput,
    LoanStatusOutput,
    LoanSummary,
    LookupByPhoneOutput,
    PolicySource,
    RecordPromiseOutput,
    SearchPolicyOutput,
    TaskCreatedOutput,
    ToolResult,
    TransactionOut,
    TransactionsOutput,
)
from supportagent.contracts.outcome import Campaign, Direction, Intent, ReasonCode

ROOT = Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 11, 9, 9, 0, tzinfo=dt.UTC)
TODAY = NOW.date()
CALLBACK_AT = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.UTC)
VARIABLES = {"bank_name": "Northwind Bank", "agent_name": "Ava"}

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


def _loan(
    last4: str,
    overdue: str,
    since: dt.date | None,
    product: str = "Personal Loan",
    status: str = "active",
) -> LoanSummary:
    return LoanSummary.model_validate(
        {
            "loan_masked": f"****{last4}",
            "product_code": "PERSONAL",
            "product_name": product,
            "outstanding_balance": "4100.00",
            "overdue_amount": overdue,
            "oldest_unpaid_due_date": since,
            "next_due_date": dt.date(2026, 12, 4),
            "next_due_amount": "200.72",
            "days_past_due": 5 if since else 0,
            "fees_accrued": "0.00",
            "status": status,
            "speakable": "overdue",
        }
    )


FRANK_LOAN = _loan("4417", "200.72", dt.date(2026, 11, 4))
OTHER_LOAN = _loan("3310", "90.00", dt.date(2026, 10, 20), "Auto Loan")


def _status(*loans: LoanSummary) -> ToolResult[LoanStatusOutput]:
    return ToolResult.success(LoanStatusOutput(loans=list(loans)))


def _row(amount: str, status: str, speakable: str) -> TransactionOut:
    return TransactionOut.model_validate(
        {
            "date": dt.date(2026, 11, 8),
            "amount": amount,
            "channel": "transfer",
            "status": status,
            "reference_masked": "****1234",
            "speakable": speakable,
        }
    )


PENDING = _row("200.72", "pending", "$200.72 by transfer on 8 November 2026 (pending, not yet posted)")
SMALL_PENDING = _row("50.00", "pending", "$50.00 by transfer on 8 November 2026 (pending, not yet posted)")
POSTED = _row("200.72", "posted", "$200.72 by transfer on 2 October 2026")


def _txns(*rows: TransactionOut) -> ToolResult[TransactionsOutput]:
    return ToolResult.success(TransactionsOutput(transactions=list(rows)))


def _policy(text: str) -> ToolResult[SearchPolicyOutput]:
    chunk = PolicySource(doc_id="late-fees", version="1.0", section="Missed", text=text, score=0.9)
    return ToolResult.success(SearchPolicyOutput(chunks=[chunk], low_confidence=False))


CONSEQUENCES = _policy("A late fee of $25 applies after the 15 day grace period; it is reported at 30 days.")
TASK = ToolResult[TaskCreatedOutput].success(
    TaskCreatedOutput.model_validate({"task_id": 7, "type": "callback", "due_at": CALLBACK_AT.isoformat()})
)
DISPUTE_FLAG = ToolResult.success(ContactFlagOutput.model_validate({"flag": "dispute", "applied": True}))


def _promise(accepted: bool, reason: str, max_date: dt.date, amount_due: str = "200.72") -> Any:
    out = RecordPromiseOutput.model_validate(
        {
            "accepted": accepted,
            "reason": reason,
            "promise_id": 5 if accepted else None,
            "max_date": max_date,
            "amount_due": amount_due,
        }
    )
    return ToolResult.success(out)


WINDOW_END = TODAY + dt.timedelta(days=14)


def _window_rules(promise_date: dt.date, amount: Decimal, loan_last4: str | None = None) -> Any:
    """A gateway that accepts a date up to `WINDOW_END` and rejects a later one (the 14 day window)."""
    if promise_date > WINDOW_END:
        return _promise(False, "date_out_of_window", WINDOW_END)
    return _promise(True, "ok", WINDOW_END)


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
    agent: Any,
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
            agent=agent,
            llm=ScriptedLLM(steps),  # type: ignore[arg-type]
            gateway=FakeGateway(FRANK, results=results),  # type: ignore[arg-type]
            direction=Direction.outbound,
            campaign=Campaign.missed_payment,
            on_start=_starter(registry, verified=verified),
        )


def _purpose(prompts: PromptRegistry) -> CollectionsPurposeAgent:
    return CollectionsPurposeAgent(prompts=prompts, variables=VARIABLES)


def _resolve(prompts: PromptRegistry, amount: str = "200.72") -> CollectionsResolveAgent:
    return CollectionsResolveAgent(
        prompts=prompts, variables=VARIABLES, loan_last4="4417", amount=Decimal(amount)
    )


def _spoken(result: ScenarioResult) -> list[str]:
    return [line.text for line in result.transcript if line.role == "agent"]


def _outputs(result: ScenarioResult) -> list[str]:
    return [c.output or "" for c in result.tool_calls]


# --------------------------------------------------------------------------- pure pieces


def test_the_purpose_line_states_the_overdue_amount_and_date_from_the_tool_result() -> None:
    line = purpose_line(FRANK_LOAN)
    assert "$200.72" in line and "4 November 2026" in line and "Personal Loan ending 4417" in line
    assert "hasn't been received yet" in line
    assert_grounded(line, [FRANK_LOAN.model_dump_json(), "$200.72", "4 November 2026", "4417"])


def test_the_subject_is_the_loan_with_the_oldest_unpaid_instalment() -> None:
    assert overdue_loan([FRANK_LOAN, OTHER_LOAN]) is OTHER_LOAN
    assert overdue_loan([_loan("1111", "0.00", None)]) is None
    assert overdue_loan([_loan("2222", "10.00", dt.date(2026, 10, 1), status="closed")]) is None


def test_only_a_pending_payment_that_covers_the_amount_counts() -> None:
    amount = Decimal("200.72")
    assert covering_pending([POSTED], amount) is None  # posted is not in transit
    assert covering_pending([SMALL_PENDING], amount) is None  # too small to cover it
    assert covering_pending([SMALL_PENDING, PENDING], amount) is PENDING


def test_the_states_register_with_their_allow_lists() -> None:
    check_registered_states()
    assert sorted(class_tool_names(CollectionsPurposeAgent)) == [
        "get_recent_transactions",
        "mark_payment_in_transit",
        "report_concern",  # shared by every state
        "set_reason_code",
    ]
    assert sorted(class_tool_names(CollectionsResolveAgent)) == [
        "offer_callback",
        "record_promise_to_pay",
        "refer_to_human",
        "report_concern",
        "search_policy",
    ]
    assert (
        flow.post_verification_state(Direction.outbound, Campaign.missed_payment) == flow.COLLECTIONS_PURPOSE
    )


# ------------------------------------------------------------------- Frank: forgot, promise at today+3


async def test_frank_hears_the_disclosure_then_the_amount_and_promises_for_three_days_later(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    pay_day = TODAY + dt.timedelta(days=3)
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [
            say("Have you already made this payment?"),
            call("set_reason_code", reason="forgot"),
            say("A late fee of $25 applies after the 15 day grace period."),
            call("record_promise_to_pay", promise_date=pay_day.isoformat()),
            say("Thank you, Frank."),
            say("Is there anything else I can help you with?"),
        ],
        {
            "get_loan_status": _status(FRANK_LOAN),
            "search_policy": CONSEQUENCES,
            "record_promise_to_pay": _window_rules,
        },
        turns=["No, I just forgot about it.", "I can pay on the twelfth."],
    )
    spoken = _spoken(result)
    disclosure = registry.render("collections").text
    assert spoken[0] == disclosure  # verbatim, from the registry, before anything else
    assert "$" not in spoken[0]
    assert "$200.72" in spoken[1] and "4 November 2026" in spoken[1]  # the amount comes after it
    assert "collections@1.0" in result.outcome["disclosures_given"]
    assert result.states == ["verify", "collections_purpose", "collections_resolve", "closing"]
    assert result.state.outcome.reason_code is ReasonCode.forgot
    promise = result.state.outcome.promise
    assert promise is not None and promise.date == pay_day and promise.amount == Decimal("200.72")
    assert Intent.promise_to_pay.value in result.outcome["intents"]
    assert result.outcome["kb_sources"] == ["late-fees@1.0#Missed"]
    assert any("On what date can you make a payment of $200.72?" in line for line in spoken)
    assert any("pay $200.72 by 12 November 2026" in line for line in spoken)  # the closing read-back
    assert result.llm.remaining == 0


async def test_the_amount_sent_to_the_gateway_is_the_codes_not_the_callers(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    pay_day = TODAY + dt.timedelta(days=3)
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [
            say("Have you already made this payment?"),
            call("set_reason_code", reason="insufficient_funds"),
            say("Noted."),
            call("record_promise_to_pay", promise_date=pay_day.isoformat()),
            say("Thank you."),
            say("Anything else?"),
        ],
        {
            "get_loan_status": _status(OTHER_LOAN, FRANK_LOAN),
            "search_policy": CONSEQUENCES,
            "record_promise_to_pay": _window_rules,
        },
        turns=["Money was tight.", "I can pay 50 dollars on the twelfth."],
    )
    sent = next(c for c in result.gateway.calls if c.tool == "record_promise_to_pay")
    assert sent.arguments["amount"] == Decimal(
        "90.00"
    )  # the older loan's overdue amount, nothing the caller said
    assert sent.arguments["loan_last4"] == "3310"
    assert "$200.72" not in _spoken(result)[1]  # the other loan's figure is never said


# --------------------------------------------------------------------------- Priya: payment in transit


async def test_priya_has_a_pending_payment_so_nothing_is_promised(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [
            say("Have you already made this payment?"),
            call("get_recent_transactions"),
            call("mark_payment_in_transit"),
            say("I can see a transfer that hasn't posted yet, so nothing more is needed now."),
            say("Is there anything else I can help you with?"),
        ],
        {
            "get_loan_status": _status(FRANK_LOAN),
            "get_recent_transactions": _txns(PENDING, POSTED),
        },
        turns=["Yes, I sent a transfer yesterday."],
    )
    assert "record_promise_to_pay" not in result.gateway.tools_called
    assert result.state.payment_in_transit is True
    assert result.outcome["payment_in_transit"] is True
    assert Intent.already_paid.value in result.outcome["intents"]
    assert result.state.outcome.promise is None and result.state.outcome.reason_code is None
    assert result.states == ["verify", "collections_purpose", "closing"]
    assert "pending, not yet posted" in _outputs(result)[0]  # the model saw the pending wording
    assert "Call `mark_payment_in_transit`" in _outputs(result)[0]


async def test_a_pending_payment_the_code_did_not_see_is_not_in_transit(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    """The model cannot mark a payment in transit on its own say-so, or when the pending one is too small."""
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [
            say("Have you already made this payment?"),
            call("mark_payment_in_transit"),  # never read the payments
            call("get_recent_transactions"),
            call("mark_payment_in_transit"),  # read, but only a small pending one
            say("I can't see a payment that covers it yet. What happened?"),
        ],
        {
            "get_loan_status": _status(FRANK_LOAN),
            "get_recent_transactions": _txns(SMALL_PENDING),
        },
        turns=["I paid it."],
    )
    assert _outputs(result).count(NOT_PENDING_NOTE) == 2
    assert result.state.payment_in_transit is False
    assert Intent.already_paid.value not in result.outcome["intents"]
    assert result.states == ["verify", "collections_purpose"]


async def test_a_failed_payment_read_says_nothing_about_a_payment(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [
            say("Have you already made this payment?"),
            call("get_recent_transactions"),
            say("I'm sorry, I can't see your payments right now. What happened with it?"),
        ],
        {
            "get_loan_status": _status(FRANK_LOAN),
            "get_recent_transactions": ToolResult.failure("upstream_error", "down"),
        },
        turns=["I already paid."],
    )
    assert "could not be read" in _outputs(result)[0]
    assert result.state.payment_in_transit is False


# --------------------------------------------------------------------------- the gateway decides the promise


async def test_a_promise_beyond_the_window_is_rejected_and_the_agent_offers_a_date_inside_it(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    too_late = TODAY + dt.timedelta(days=20)
    result = await _run(
        prompts,
        registry,
        _resolve(prompts),
        [
            say("A late fee applies."),
            call("record_promise_to_pay", promise_date=too_late.isoformat()),
            say("The latest date I can note is 23 November 2026. Does that work?"),
            call("record_promise_to_pay", promise_date=WINDOW_END.isoformat()),
            say("Thank you."),
            say("Anything else?"),
        ],
        {"search_policy": CONSEQUENCES, "record_promise_to_pay": _window_rules},
        turns=["I can pay on the twenty-ninth.", "Fine, the twenty-third."],
    )
    first = _outputs(result)[0]
    assert (
        "23 November 2026" in first and "2026-11-23" in first
    )  # the gateway's own max_date, rendered in code
    assert "$" not in first
    sent = [c.arguments["promise_date"] for c in result.gateway.calls if c.tool == "record_promise_to_pay"]
    assert sent == [too_late, WINDOW_END]
    promise = result.state.outcome.promise
    assert promise is not None and promise.date == WINDOW_END  # the rejected date was never recorded
    assert result.states[-1] == "closing"


async def test_an_amount_the_gateway_corrects_is_the_amount_asked_for_next(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    pay_day = TODAY + dt.timedelta(days=2)

    def respond(promise_date: dt.date, amount: Decimal, loan_last4: str | None = None) -> Any:
        if amount != Decimal("190.00"):
            return _promise(False, "amount_invalid", WINDOW_END, amount_due="190.00")
        return _promise(True, "ok", WINDOW_END, amount_due="190.00")

    result = await _run(
        prompts,
        registry,
        _resolve(prompts),
        [
            say("A late fee applies."),
            call("record_promise_to_pay", promise_date=pay_day.isoformat()),
            say("The amount I can note is $190.00. Is that right?"),
            call("record_promise_to_pay", promise_date=pay_day.isoformat()),
            say("Thank you."),
            say("Anything else?"),
        ],
        {"search_policy": CONSEQUENCES, "record_promise_to_pay": respond},
        turns=["I can pay on the eleventh.", "Yes."],
    )
    assert "$190.00" in _outputs(result)[0]
    amounts = [c.arguments["amount"] for c in result.gateway.calls if c.tool == "record_promise_to_pay"]
    assert amounts == [Decimal("200.72"), Decimal("190.00")]
    promise = result.state.outcome.promise
    assert promise is not None and promise.amount == Decimal("190.00")


async def test_after_three_refused_dates_the_agent_offers_a_callback_and_promises_nothing_else(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    late = (TODAY + dt.timedelta(days=30)).isoformat()
    result = await _run(
        prompts,
        registry,
        _resolve(prompts),
        [
            say("A late fee applies."),
            call("record_promise_to_pay", promise_date=late),
            say("Latest 23 November."),
            call("record_promise_to_pay", promise_date=late),
            say("Latest 23 November."),
            call("record_promise_to_pay", promise_date=late),
            say("No date has been agreed. Would you like a call back?"),
            call("offer_callback"),
            say("A colleague will call you back."),
        ],
        {"search_policy": CONSEQUENCES, "record_promise_to_pay": _window_rules, "request_callback": TASK},
        turns=["Next month.", "Still next month.", "Next month, really.", "Yes, call me."],
    )
    assert "No date has been agreed" in _outputs(result)[2]
    assert result.state.outcome.promise is None
    assert result.state.outcome.callback is not None  # booked by the gateway
    assert "10 November 2026" in _outputs(result)[3]  # the time the gateway returned, not the request


async def test_a_promise_the_gateway_fails_to_record_is_not_promised(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    day = (TODAY + dt.timedelta(days=3)).isoformat()
    result = await _run(
        prompts,
        registry,
        _resolve(prompts),
        [
            say("A late fee applies."),
            call("record_promise_to_pay", promise_date=day),
            say("I'm sorry, I couldn't record that."),
        ],
        {
            "search_policy": CONSEQUENCES,
            "record_promise_to_pay": ToolResult.failure("upstream_error", "down"),
        },
        turns=["I'll pay on the twelfth."],
    )
    assert _outputs(result)[0] == PROMISE_FAILED
    assert result.state.outcome.promise is None
    assert Intent.promise_to_pay.value not in result.outcome["intents"]


async def test_an_unreadable_date_never_reaches_the_gateway(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _resolve(prompts),
        [
            say("A late fee applies."),
            call("record_promise_to_pay", promise_date="next friday"),
            say("Which day?"),
        ],
        {"search_policy": CONSEQUENCES, "record_promise_to_pay": _window_rules},
        turns=["Next Friday."],
    )
    assert "record_promise_to_pay" not in result.gateway.tools_called
    assert "not clear" in _outputs(result)[0]


async def test_a_callback_the_gateway_refuses_is_not_promised(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _resolve(prompts),
        [say("A late fee applies."), call("offer_callback"), say("I'm sorry, I couldn't arrange that.")],
        {"search_policy": CONSEQUENCES, "request_callback": ToolResult.failure("upstream_error", "down")},
        turns=["Please call me back."],
    )
    assert _outputs(result)[0] == CALLBACK_FAILED
    assert result.state.outcome.callback is None


# ------------------------------------------------------------------- hardship and dispute: a human path


async def test_hana_income_disruption_is_a_hardship_referral_in_code(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    """`set_reason_code(income_disruption)` refers to a human: no promise is asked for. Until
    escalation exists (T-AGENT-11) the referral is the honest callback line, spoken from what the gateway
    booked."""
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [
            say("Have you already made this payment?"),
            call("set_reason_code", reason="income_disruption"),
            say("Is there anything else I can help you with?"),
        ],
        {"get_loan_status": _status(FRANK_LOAN), "request_callback": TASK},
        turns=["No, I would rather not say."],
    )
    assert result.state.outcome.reason_code is ReasonCode.income_disruption
    assert Intent.hardship.value in result.outcome["intents"]
    assert "record_promise_to_pay" not in result.gateway.tools_called
    assert result.gateway.tools_called.count("request_callback") == 1
    assert any("call you back on 10 November 2026" in line for line in _spoken(result))
    assert result.states[-1] == "closing"
    assert result.state.outcome.promise is None


async def test_a_hardship_the_caller_states_is_caught_before_the_model_replies(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _resolve(prompts),
        [
            say("A late fee applies."),
            say("Is there anything else I can help you with?"),
        ],  # the model is not asked about the caller's turn, only by closing
        {"search_policy": CONSEQUENCES, "request_callback": TASK},
        turns=["I lost my job and I can't pay anything right now."],
    )
    assert Intent.hardship.value in result.outcome["intents"]
    assert (
        result.state.outcome.reason_code is ReasonCode.income_disruption
    )  # noted in code from the caller's words
    assert result.llm.remaining == 0
    assert "record_promise_to_pay" not in result.gateway.tools_called
    assert result.gateway.tools_called.count("request_callback") == 1
    assert result.states[-1] == "closing"


async def test_dawit_a_dispute_sets_the_flag_and_goes_to_a_human_path(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [say("Have you already made this payment?"), say("Is there anything else I can help you with?")],
        {"get_loan_status": _status(FRANK_LOAN), "set_contact_flag": DISPUTE_FLAG, "request_callback": TASK},
        turns=["I dispute this. I never agreed to this amount."],
    )
    assert result.state.outcome.dispute_flag is True
    assert result.state.outcome.reason_code is ReasonCode.dispute
    assert Intent.dispute.value in result.outcome["intents"]
    flags = [c.arguments["flag"] for c in result.gateway.calls if c.tool == "set_contact_flag"]
    assert flags == ["dispute"]
    assert result.gateway.tools_called.count("request_callback") == 1
    assert "record_promise_to_pay" not in result.gateway.tools_called
    assert result.states[-1] == "closing"


async def test_a_dispute_reason_code_is_a_referral_not_a_promise_request(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [
            say("Have you already made this payment?"),
            call("set_reason_code", reason="dispute"),
            say("Is there anything else I can help you with?"),
        ],
        {"get_loan_status": _status(FRANK_LOAN), "set_contact_flag": DISPUTE_FLAG, "request_callback": TASK},
        turns=["No, I have not paid it."],
    )
    assert result.state.outcome.reason_code is ReasonCode.dispute
    assert result.state.outcome.dispute_flag is True
    assert "record_promise_to_pay" not in result.gateway.tools_called


async def test_a_referral_the_gateway_cannot_book_promises_nothing(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [
            say("Have you already made this payment?"),
            call("set_reason_code", reason="income_disruption"),
            say("Is there anything else I can help you with?"),
        ],
        {
            "get_loan_status": _status(FRANK_LOAN),
            "request_callback": ToolResult.failure("upstream_error", "down"),
        },
        turns=["No, I lost my income."],
    )
    spoken = " ".join(_spoken(result))
    assert "couldn't arrange a call back" in spoken
    assert "will call you back" not in spoken and "call you back on" not in spoken
    assert result.state.outcome.callback is None


async def test_the_model_can_refer_a_hardship_with_refer_to_human(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _resolve(prompts),
        [
            say("A late fee applies."),
            call("refer_to_human", reason="hardship"),
            say("Is there anything else I can help you with?"),
        ],
        {"search_policy": CONSEQUENCES, "request_callback": TASK},
        turns=["I really can't manage this month."],
    )
    assert result.gateway.tools_called.count("request_callback") == 1
    assert result.states[-1] == "closing"
    assert "record_promise_to_pay" not in result.gateway.tools_called


# --------------------------------------------------------------------------- safe paths


async def test_an_unverified_caller_gets_no_disclosure_no_figure_and_goes_to_verification(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [say("Please tell me your full name, date of birth and the last four digits of your loan.")],
        {"get_loan_status": _status(FRANK_LOAN)},
        verified=False,
    )
    assert "get_loan_status" not in result.gateway.tools_called
    assert "$" not in " ".join(_spoken(result)[:1])
    assert result.outcome["disclosures_given"] == []  # owed once verified, before any amount
    assert result.states[-1] == "verify"


async def test_nothing_overdue_says_no_figure_and_closes_after_the_disclosure(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [say("Is there anything else I can help you with?")],
        {"get_loan_status": _status(_loan("4417", "0.00", None))},
    )
    assert "$" not in " ".join(_spoken(result))
    assert result.states == ["verify", "collections_purpose", "closing"]


async def test_when_the_account_system_fails_no_figure_is_said(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [say("Is there anything else I can help you with?")],
        {"get_loan_status": ToolResult.failure("upstream_error", "down")},
    )
    assert "$" not in " ".join(_spoken(result))
    assert result.states == ["verify", "collections_purpose", "closing"]


async def test_a_farewell_closes_the_call_without_the_model(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    result = await _run(
        prompts,
        registry,
        _purpose(prompts),
        [say("Have you already made this payment?")],
        {"get_loan_status": _status(FRANK_LOAN)},
        turns=["Goodbye."],
    )
    assert result.states[-1] == "closing"
    assert result.state.end_reason == flow.END_BY_CALLER


# --------------------------------------------------------------------------- the whole outbound path


async def test_a_missed_payment_call_goes_greeting_verify_collections_with_the_disclosure_before_the_amount(
    prompts: PromptRegistry, registry: DisclosureRegistry
) -> None:
    """Frank answers, verifies, and the campaign table sends him to collections, never to the reminder."""

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
                    say("Have you already made this payment?"),
                ]
            ),
            gateway=FakeGateway(
                FRANK,
                {
                    "lookup_by_phone": ToolResult.success(
                        LookupByPhoneOutput(match=True, first_name="Frank")
                    ),
                    "get_loan_status": _status(FRANK_LOAN),
                },
            ),
            direction=Direction.outbound,
            campaign=Campaign.missed_payment,
            on_start=start,
        )
    assert result.states == ["greeting", "verify", "collections_purpose"]
    assert result.outcome["disclosures_given"] == ["ai_recording@1.0", "collections@1.0"]
    spoken = _spoken(result)
    collections_at = next(i for i, line in enumerate(spoken) if line == registry.render("collections").text)
    first_amount_at = next(i for i, line in enumerate(spoken) if "$" in line)
    assert collections_at < first_amount_at  # the disclosure is always before the first amount
    assert "reminder" not in " ".join(spoken).lower()
