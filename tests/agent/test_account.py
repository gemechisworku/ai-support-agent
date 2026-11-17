"""T-AGENT-06 · AGENT-R5: account questions in text mode with the scripted LLM and a fake gateway.

The caller's question is already in the chat history when the router hands over, so each scenario starts in
`AccountAgent` with that question seeded; the state's entry action answers it. Every figure the agent says
must appear in a tool result from the same call (`assert_grounded`, reused by EVAL-03).
"""

import datetime as dt
from decimal import Decimal

import pytest
from livekit.agents.llm import ChatContext

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent.base import check_registered_states, class_tool_names
from supportagent.agent.context import CallState
from supportagent.agent.grounding import assert_grounded, numbers_in, ungrounded_numbers
from supportagent.agent.prompts.registry import DEFAULT_PROMPTS_DIR, PromptRegistry
from supportagent.agent.states import flow
from supportagent.agent.states.account import (
    CLARIFY_LOAN,
    NOT_VERIFIED_NOTE,
    PAYOFF_CAVEAT,
    UNAVAILABLE_NOTE,
    AccountAgent,
)
from supportagent.agent.states.router import ANYTHING_ELSE
from supportagent.agent.testing import FakeGateway, ScenarioResult, ScriptedLLM, call, run_text_scenario, say
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.gateway import (
    LoanStatusOutput,
    LoanSummary,
    ToolResult,
    TransactionOut,
    TransactionsOutput,
)
from supportagent.contracts.outcome import Campaign, Direction

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

PERSONAL = LoanSummary(
    loan_masked="****4417",
    product_code="PERSONAL",
    product_name="Personal Loan",
    outstanding_balance=Decimal("3450.25"),
    overdue_amount=Decimal("200.72"),
    oldest_unpaid_due_date=dt.date(2026, 11, 4),
    next_due_date=dt.date(2026, 12, 4),
    next_due_amount=Decimal("200.72"),
    days_past_due=5,
    fees_accrued=Decimal("0.00"),
    status="active",
    speakable="$200.72 overdue since 4 November 2026; next payment $200.72 due on 4 December 2026",
)
AUTO = LoanSummary(
    loan_masked="****3310",
    product_code="AUTO",
    product_name="Auto Loan",
    outstanding_balance=Decimal("12800.00"),
    overdue_amount=Decimal("0.00"),
    oldest_unpaid_due_date=None,
    next_due_date=dt.date(2026, 11, 25),
    next_due_amount=Decimal("415.30"),
    days_past_due=0,
    fees_accrued=Decimal("0.00"),
    status="active",
    speakable="next payment $415.30 due on 25 November 2026",
)
POSTED = TransactionOut(
    date=dt.date(2026, 10, 30),
    amount=Decimal("200.72"),
    channel="mobile",
    status="posted",
    reference_masked="****8812",
    speakable="$200.72 on 30 October 2026 by mobile",
)
PENDING = TransactionOut(
    date=dt.date(2026, 11, 8),
    amount=Decimal("150.00"),
    channel="branch",
    status="pending",
    reference_masked="****7731",
    speakable="$150.00 on 8 November 2026 by branch (pending, not yet posted)",
)


def _status(*loans: LoanSummary) -> ToolResult[LoanStatusOutput]:
    return ToolResult.success(LoanStatusOutput(loans=list(loans)))


def _by_last4(*loans: LoanSummary):  # type: ignore[no-untyped-def]
    """A canned `get_loan_status` that honours `loan_last4` the way the gateway does."""

    def respond(loan_last4: str | None = None) -> ToolResult[LoanStatusOutput]:
        chosen = [ln for ln in loans if loan_last4 is None or ln.loan_masked.endswith(loan_last4)]
        return _status(*chosen)

    return respond


def _transactions(*rows: TransactionOut) -> ToolResult[TransactionsOutput]:
    return ToolResult.success(TransactionsOutput(transactions=list(rows)))


@pytest.fixture
def prompts() -> PromptRegistry:
    """The real prompt bundle (account and router included)."""
    return PromptRegistry(DEFAULT_PROMPTS_DIR)


async def _verified(state: CallState) -> None:
    """The caller got here from the router, already verified (that is the only way into this state)."""
    state.verified = True
    state.first_name = "Frank"
    state.states_visited.append("router")


async def _unverified(state: CallState) -> None:
    state.states_visited.append("router")


async def _run(
    prompts: PromptRegistry,
    question: str,
    steps: list[object],
    results: dict[str, object],
    *,
    verified: bool = True,
    turns: list[str] | None = None,
) -> ScenarioResult:
    ctx = ChatContext()
    ctx.add_message(role="user", content=question)
    return await run_text_scenario(
        FRANK,
        turns or [],
        agent=AccountAgent(prompts=prompts, chat_ctx=ctx),
        llm=ScriptedLLM(steps),  # type: ignore[arg-type]
        gateway=FakeGateway(FRANK, results=results),  # type: ignore[arg-type]
        direction=Direction.inbound,
        campaign=Campaign.inbound,
        on_start=_verified if verified else _unverified,
    )


def _spoken(result: ScenarioResult) -> list[str]:
    return [line.text for line in result.transcript if line.role == "agent"]


def _outputs(result: ScenarioResult) -> list[str]:
    return [c.output or "" for c in result.tool_calls]


# --------------------------------------------------------------------------- the grounding helper


def test_numbers_are_compared_as_whole_tokens() -> None:
    assert numbers_in("$1,245.10 due on 4 December 2026, ending 4417.") == {
        "1,245.10",
        "4",
        "2026",
        "4417",
    }


def test_a_figure_not_in_a_source_is_ungrounded() -> None:
    sources = ["$200.72 overdue since 4 November 2026"]
    assert ungrounded_numbers("You owe $200.72 since 4 November 2026.", sources) == []
    assert ungrounded_numbers("You owe $200.00 since 4 November 2026.", sources) == ["200.00"]
    assert ungrounded_numbers("You owe about $200.", sources) == ["200"]  # a rounded figure is not verbatim


def test_assert_grounded_names_the_figures() -> None:
    with pytest.raises(AssertionError, match=r"300\.00"):
        assert_grounded("You owe $300.00.", ["$200.72"])
    assert_grounded("No figures here at all.", [])


# --------------------------------------------------------------------------- balance, due date, status


async def test_balance_comes_from_the_tool_result(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "How much do I still owe on my loan?",
        [
            call("get_loan_status"),
            say("Your outstanding balance on the Personal Loan ending 4417 is $3,450.25."),
        ],
        {"get_loan_status": _by_last4(PERSONAL)},
    )
    assert result.states == ["router", "account"]
    assert result.gateway.tools_called == ["get_loan_status"]
    assert result.gateway.calls[0].arguments == {"loan_last4": None}
    assert "$3,450.25" in _outputs(result)[0]
    assert result.llm.remaining == 0
    for line in _spoken(result):
        assert_grounded(line, _outputs(result))


async def test_the_next_due_date_and_amount_use_the_speakable_text(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "When is my next payment due, and how much?",
        [
            call("get_loan_status"),
            say("Your next payment of $200.72 is due on 4 December 2026."),
        ],
        {"get_loan_status": _by_last4(PERSONAL)},
    )
    assert PERSONAL.speakable in _outputs(result)[0]  # the gateway's own wording, verbatim
    for line in _spoken(result):
        assert_grounded(line, _outputs(result))


async def test_loan_status_reports_days_past_due_from_the_tool(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "Am I behind on my loan?",
        [call("get_loan_status"), say("The loan is active and 5 days past due.")],
        {"get_loan_status": _by_last4(PERSONAL)},
    )
    assert "status active" in _outputs(result)[0] and "5 days past due" in _outputs(result)[0]
    for line in _spoken(result):
        assert_grounded(line, _outputs(result))


async def test_a_loan_with_nothing_overdue_has_no_days_past_due_line(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "Am I behind?",
        [call("get_loan_status"), say("You are up to date.")],
        {"get_loan_status": _by_last4(AUTO)},
    )
    assert "past due" not in _outputs(result)[0]


# --------------------------------------------------------------------------- payoff estimate


async def test_the_payoff_estimate_is_labelled_an_estimate_with_the_caveat(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "What would it take to pay the whole loan off?",
        [
            call("get_loan_status"),
            say(
                "As an estimate, about $3,450.25. The exact amount depends on the payoff date and any "
                "fees, and the formal payoff quote is issued in writing."
            ),
        ],
        {"get_loan_status": _by_last4(PERSONAL)},
    )
    out = _outputs(result)[0]
    assert "Payoff estimate" in out and "about $3,450.25" in out and PAYOFF_CAVEAT in out
    for line in _spoken(result):
        assert_grounded(line, _outputs(result))


# --------------------------------------------------------------------------- last payment


async def test_last_payment_reads_the_transaction_speakable(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "Did my last payment go through?",
        [
            call("get_recent_transactions"),
            say("Your last payment was $200.72 on 30 October 2026 by mobile."),
        ],
        {
            "get_loan_status": _by_last4(PERSONAL),
            "get_recent_transactions": _transactions(POSTED),
        },
    )
    assert POSTED.speakable in _outputs(result)[0]
    for line in _spoken(result):
        assert_grounded(line, _outputs(result))


async def test_a_pending_payment_is_said_to_be_pending(prompts: PromptRegistry) -> None:
    """Priya's case: the payment is in but not posted, so the overdue amount can still show."""
    result = await _run(
        prompts,
        "I paid on Sunday, did it arrive?",
        [
            call("get_recent_transactions"),
            say("Your payment of $150.00 on 8 November 2026 by branch is pending, not yet posted."),
        ],
        {
            "get_loan_status": _by_last4(PERSONAL),
            "get_recent_transactions": _transactions(PENDING, POSTED),
        },
    )
    out = _outputs(result)[0]
    assert "pending, not yet posted" in out
    assert "not posted yet" in out  # the tool also tells the model to say it has not posted
    assert out.index(PENDING.speakable) < out.index(POSTED.speakable)  # newest first, as the gateway returns
    for line in _spoken(result):
        assert_grounded(line, _outputs(result))


async def test_no_transactions_is_said_plainly_with_no_figures(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "What was my last payment?",
        [call("get_recent_transactions"), say("I don't see any recent payments on this loan.")],
        {"get_loan_status": _by_last4(PERSONAL), "get_recent_transactions": _transactions()},
    )
    assert "No recent payments" in _outputs(result)[0]
    assert numbers_in(_outputs(result)[0]) == set()


# --------------------------------------------------------------------------- more than one loan


async def test_two_loans_get_a_clarifying_question_before_any_figure(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "How much do I owe?",
        [
            call("get_loan_status"),
            say("Which loan do you mean, the Personal Loan ending 4417 or the Auto Loan ending 3310?"),
            call("get_loan_status", loan_last4="3310"),
            say("The Auto Loan's next payment is $415.30 due on 25 November 2026."),
        ],
        {"get_loan_status": _by_last4(PERSONAL, AUTO)},
        turns=["The auto loan."],
    )
    first, second = _outputs(result)
    assert CLARIFY_LOAN in first
    assert "Personal Loan ending 4417" in first and "Auto Loan ending 3310" in first
    assert "$" not in first  # no amount before the caller has chosen
    assert [c.arguments for c in result.gateway.calls] == [{"loan_last4": None}, {"loan_last4": "3310"}]
    assert "$415.30" in second and "$200.72" not in second  # only the chosen loan's figures
    for line in _spoken(result):
        assert_grounded(line, _outputs(result))


async def test_a_last4_that_matches_nothing_asks_again(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "How much do I owe on the 9999 loan?",
        [
            call("get_loan_status", loan_last4="9999"),
            say("I don't see that one. Is it the 4417 or the 3310?"),
        ],
        {"get_loan_status": lambda loan_last4=None: _status() if loan_last4 else _status(PERSONAL, AUTO)},
    )
    out = _outputs(result)[0]
    assert CLARIFY_LOAN in out and "ending 4417" in out and "ending 3310" in out
    assert "$" not in out
    assert [c.arguments for c in result.gateway.calls] == [{"loan_last4": "9999"}, {"loan_last4": None}]


async def test_a_badly_formed_last4_is_asked_for_again_without_calling_the_gateway(
    prompts: PromptRegistry,
) -> None:
    result = await _run(
        prompts,
        "The one ending in forty-four",
        [call("get_loan_status", loan_last4="44"), say("Could you give me all four digits?")],
        {"get_loan_status": _by_last4(PERSONAL, AUTO)},
    )
    assert result.gateway.calls == []
    assert "four digits" in _outputs(result)[0]


async def test_last_payment_with_two_loans_asks_which_loan_first(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "What was my last payment?",
        [
            call("get_recent_transactions"),
            say("Which loan, the Personal Loan or the Auto Loan?"),
            call("get_recent_transactions", loan_last4="4417"),
            say("It was $200.72 on 30 October 2026 by mobile."),
        ],
        {
            "get_loan_status": _by_last4(PERSONAL, AUTO),
            "get_recent_transactions": _transactions(POSTED),
        },
        turns=["The personal loan."],
    )
    # The loan list is fetched once to learn there are two; the transactions are read only when scoped.
    assert result.gateway.tools_called == ["get_loan_status", "get_recent_transactions"]
    assert result.gateway.calls[1].arguments == {"loan_last4": "4417"}
    assert CLARIFY_LOAN in _outputs(result)[0]
    for line in _spoken(result):
        assert_grounded(line, _outputs(result))


async def test_one_loan_is_never_asked_about(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "What was my last payment?",
        [call("get_recent_transactions"), say("It was $200.72 on 30 October 2026 by mobile.")],
        {"get_loan_status": _by_last4(PERSONAL), "get_recent_transactions": _transactions(POSTED)},
    )
    assert CLARIFY_LOAN not in _outputs(result)[0]
    assert result.gateway.calls[-1].arguments == {"loan_last4": None}


# --------------------------------------------------------------------------- failures and the guard


async def test_a_gateway_error_gives_no_figures_and_no_detail(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "What do I owe?",
        [call("get_loan_status"), say("I can't get that right now. A colleague can follow up.")],
        {"get_loan_status": ToolResult.failure("upstream_error", "boom: connection to bank-db refused")},
    )
    out = _outputs(result)[0]
    assert out == UNAVAILABLE_NOTE
    assert "boom" not in out and numbers_in(out) == set()


async def test_an_unverified_caller_gets_nothing_and_the_gateway_is_not_reached(
    prompts: PromptRegistry,
) -> None:
    result = await _run(
        prompts,
        "What do I owe?",
        [call("get_loan_status"), say("I need to verify who you are first.")],
        {"get_loan_status": _by_last4(PERSONAL)},
        verified=False,
    )
    assert _outputs(result)[0] == NOT_VERIFIED_NOTE
    assert result.gateway.calls == []  # the agent-side guard (VerifiedGateway) answered, not the gateway


async def test_the_question_is_answered_on_entry_without_the_caller_speaking_again(
    prompts: PromptRegistry,
) -> None:
    result = await _run(
        prompts,
        "What do I owe?",
        [say("One moment.")],
        {},
    )
    assert _spoken(result) == ["One moment."]  # `generate_reply()` ran from `on_state_enter`
    assert result.llm.remaining == 0


# --------------------------------------------------------------------------- hand-back and allow-list


async def test_finish_returns_to_the_router(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts,
        "What do I owe?",
        [call("get_loan_status"), call("finish")],
        {"get_loan_status": _by_last4(PERSONAL)},
    )
    assert result.states == ["router", "account", "router"]
    assert _spoken(result)[-1] == ANYTHING_ELSE


def test_the_tool_allow_list() -> None:
    assert set(class_tool_names(AccountAgent)) == {
        "get_loan_status",
        "get_recent_transactions",
        "finish",
        "report_concern",
    }
    assert "record_call_outcome" not in class_tool_names(AccountAgent)
    check_registered_states()  # at most four domain tools, no forbidden one, for every registered state


def test_the_state_is_registered_under_the_name_the_router_uses() -> None:
    assert AccountAgent.state_name == flow.ACCOUNT == "account"
    assert flow.resolve(flow.ACCOUNT) is AccountAgent
