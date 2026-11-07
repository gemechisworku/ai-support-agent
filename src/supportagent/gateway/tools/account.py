"""Verified-scope read tools (GW-R4, GW-R5): loan status, recent transactions, interaction history.

Every tool reads only the bound customer's data; an optional `loan_last4` narrows to one of *their*
loans, and an unknown last 4 is `not_found` without revealing anything about other customers.
Outputs are built field by field: amounts and dates exactly as the bank returns them, numbers
masked to the last 4 digits, plus `speakable` strings rendered from the same `Decimal` values.
"""

import re

from supportagent.contracts import bank as c
from supportagent.contracts.gateway import (
    CallSummary,
    GetLoanStatusInput,
    GetTransactionsInput,
    InteractionHistoryInput,
    InteractionHistoryOutput,
    LoanStatusOutput,
    LoanSummary,
    PromiseOut,
    TaskOut,
    TransactionOut,
    TransactionsOutput,
)
from supportagent.gateway.masking import speakable_date, speakable_money
from supportagent.gateway.tools import ToolContext, ToolDenied, gateway_tool

MAX_TRANSACTIONS = 5
HISTORY_CALLS = 5
_LONG_DIGITS = re.compile(r"\d{5,}")


async def customer_loans(ctx: ToolContext, loan_last4: str | None) -> list[c.Loan]:
    """The bound customer's loans, optionally the one ending in `loan_last4` (else `not_found`)."""
    loans = await ctx.gateway.bank.customer_loans(ctx.customer_id)
    if loan_last4 is None:
        return loans
    picked = [ln for ln in loans if ln.number[-4:] == loan_last4]
    if not picked:
        raise ToolDenied("not_found", "no loan of yours ends in those digits")
    return picked


def loan_speakable(s: c.LoanStatus) -> str:
    parts = []
    if s.overdue_amount > 0 and s.oldest_unpaid_due_date is not None:
        parts.append(
            f"{speakable_money(s.overdue_amount)} overdue since {speakable_date(s.oldest_unpaid_due_date)}"
        )
    if s.next_due_date is not None and s.next_due_amount is not None:
        parts.append(
            f"next payment {speakable_money(s.next_due_amount)} due on {speakable_date(s.next_due_date)}"
        )
    return "; ".join(parts) or "no payment due"


def summarise(s: c.LoanStatus) -> LoanSummary:
    return LoanSummary(
        loan_masked=f"****{s.number_last4}",  # the bank already returns only the last 4
        product_code=s.product_code,
        product_name=s.product_name,
        outstanding_balance=s.outstanding_balance,
        overdue_amount=s.overdue_amount,
        oldest_unpaid_due_date=s.oldest_unpaid_due_date,
        next_due_date=s.next_due_date,
        next_due_amount=s.next_due_amount,
        days_past_due=s.days_past_due,
        fees_accrued=s.fees_accrued,
        status=s.status,
        speakable=loan_speakable(s),
    )


@gateway_tool(
    "get_loan_status", scope="verified", input_model=GetLoanStatusInput, output_model=LoanStatusOutput
)
async def get_loan_status(ctx: ToolContext, inp: GetLoanStatusInput) -> LoanStatusOutput:
    summaries = []
    for loan in await customer_loans(ctx, inp.loan_last4):
        status = await ctx.gateway.bank.loan_status(loan.id)
        if status is not None:
            summaries.append(summarise(status))
    return LoanStatusOutput(loans=summaries)


def _mask_reference(reference: str) -> str:
    return _LONG_DIGITS.sub(lambda m: f"****{m.group(0)[-4:]}", reference)


def transaction_out(t: c.Transaction) -> TransactionOut:
    when = t.ts.date()
    spoken = f"{speakable_money(t.amount)} on {speakable_date(when)} by {t.channel}"
    return TransactionOut(
        date=when,
        amount=t.amount,
        channel=t.channel,
        status=t.status,
        reference_masked=_mask_reference(t.reference),
        speakable=spoken + (" (pending, not yet posted)" if t.status == "pending" else ""),
    )


@gateway_tool(
    "get_recent_transactions",
    scope="verified",
    input_model=GetTransactionsInput,
    output_model=TransactionsOutput,
)
async def get_recent_transactions(ctx: ToolContext, inp: GetTransactionsInput) -> TransactionsOutput:
    rows: list[c.Transaction] = []
    for loan in await customer_loans(ctx, inp.loan_last4):
        rows += await ctx.gateway.bank.transactions(
            loan.repayment_account_id, loan_id=loan.id, limit=MAX_TRANSACTIONS
        )
    rows.sort(key=lambda t: (t.ts, t.id), reverse=True)
    return TransactionsOutput(transactions=[transaction_out(t) for t in rows[:MAX_TRANSACTIONS]])


@gateway_tool(
    "get_interaction_history",
    scope="verified",
    input_model=InteractionHistoryInput,
    output_model=InteractionHistoryOutput,
)
async def get_interaction_history(ctx: ToolContext, _: InteractionHistoryInput) -> InteractionHistoryOutput:
    """Structured continuity only — dates, results, promises, tasks; no free-text notes or summaries."""
    history = await ctx.gateway.bank.interaction_history(ctx.customer_id, limit=HISTORY_CALLS)
    return InteractionHistoryOutput(
        recent_calls=[
            CallSummary(
                date=i.started_at.date(),
                direction=i.direction,
                campaign=i.campaign,
                contact_result=i.contact_result,
            )
            for i in history.interactions
        ],
        open_promises=[
            PromiseOut(promised_date=p.promised_date, amount=p.amount, status=p.status)
            for p in history.open_promises
        ],
        open_tasks=[TaskOut(type=t.type, due_at=t.due_at, status=t.status) for t in history.open_tasks],
    )
