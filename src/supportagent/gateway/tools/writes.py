"""Write tools (GW-R6, GW-R10): promises, callbacks, escalations, contact flags and the call outcome.

They write only to interaction tables — promises, tasks, contact preferences, interactions — through
the bank API; nothing here can change a balance, loan, instalment or transaction. Business rules are
code: a promise must fall within today..today+`promise_max_days` and 0 < amount ≤ amount due, using
the bank's own "today" (`LoanStatus.as_of`) and figures.

Tasks need a customer (the bank's `TaskIn.customer_id`): the bound customer, else the customer the
call is about (outbound) or the caller's only registered customer (inbound). Without one, `escalate`
still returns the transfer target (no ticket) and `request_callback` is `not_found`.
"""

import datetime as dt
from decimal import Decimal
from typing import Literal

from supportagent.common import clock
from supportagent.contracts import bank as c
from supportagent.contracts.gateway import (
    ContactFlagOutput,
    EscalateInput,
    EscalateOutput,
    OutcomeRecordedOutput,
    RecordPromiseInput,
    RecordPromiseOutput,
    RequestCallbackInput,
    SetContactFlagInput,
    TaskCreatedOutput,
)
from supportagent.contracts.outcome import CallOutcome
from supportagent.gateway.masking import sanitize_text
from supportagent.gateway.tools import ToolContext, ToolDenied, gateway_tool
from supportagent.gateway.tools.account import customer_loans

MAX_CALLBACK_AHEAD = dt.timedelta(days=30)
ZERO = Decimal("0.00")


def amount_due(status: c.LoanStatus) -> Decimal:
    """What a promise may cover: the overdue amount, or the next instalment when nothing is overdue."""
    if status.overdue_amount > 0:
        return status.overdue_amount
    return status.next_due_amount or ZERO


async def _promise_loan(ctx: ToolContext, loan_last4: str | None) -> c.Loan | None:
    try:
        loans = await customer_loans(ctx, loan_last4)
    except ToolDenied:
        return None
    for loan in loans:  # prefer the loan the call is about
        if loan.id == ctx.session.loan_id:
            return loan
    return loans[0] if loans else None


@gateway_tool(
    "record_promise_to_pay",
    scope="verified",
    input_model=RecordPromiseInput,
    output_model=RecordPromiseOutput,
)
async def record_promise_to_pay(ctx: ToolContext, inp: RecordPromiseInput) -> RecordPromiseOutput:
    max_days = dt.timedelta(days=ctx.gateway.config.promise_max_days)
    loan = await _promise_loan(ctx, inp.loan_last4)
    status = await ctx.gateway.bank.loan_status(loan.id) if loan else None
    if loan is None or status is None:
        today = clock.today()
        return RecordPromiseOutput(
            accepted=False, reason="loan_not_found", max_date=today + max_days, amount_due=ZERO
        )
    today, due = status.as_of, amount_due(status)
    reply = {"max_date": today + max_days, "amount_due": due}
    if not today <= inp.promise_date <= today + max_days:
        return RecordPromiseOutput(accepted=False, reason="date_out_of_window", **reply)
    partial_ok = ctx.gateway.config.allow_partial or inp.amount == due
    if not (ZERO < inp.amount <= due) or not partial_ok:
        return RecordPromiseOutput(accepted=False, reason="amount_invalid", **reply)
    promise = await ctx.gateway.bank.create_promise(
        c.PromiseIn(
            call_id=ctx.session.call_id, loan_id=loan.id, promised_date=inp.promise_date, amount=inp.amount
        )
    )
    if promise is None:
        return RecordPromiseOutput(accepted=False, reason="loan_not_found", **reply)
    return RecordPromiseOutput(accepted=True, reason="ok", promise_id=promise.id, **reply)


def task_customer(ctx: ToolContext) -> int | None:
    s = ctx.session
    if s.bound_customer_id is not None:
        return s.bound_customer_id
    if s.expected_customer_id is not None:
        return s.expected_customer_id
    return s.candidate_ids[0] if len(s.candidate_ids) == 1 else None


async def _create_task(
    ctx: ToolContext,
    customer_id: int,
    kind: Literal["callback", "escalation"],
    reason: str,
    summary: str,
    due_at: dt.datetime,
) -> c.Task:
    task = await ctx.gateway.bank.create_task(
        c.TaskIn(
            call_id=ctx.session.call_id,
            customer_id=customer_id,
            loan_id=ctx.session.loan_id,
            type=kind,
            reason=reason,
            summary=summary,
            due_at=due_at,
        )
    )
    if task is None:
        raise ToolDenied("not_found")
    return task


@gateway_tool(
    "request_callback", scope="any", input_model=RequestCallbackInput, output_model=TaskCreatedOutput
)
async def request_callback(ctx: ToolContext, inp: RequestCallbackInput) -> TaskCreatedOutput:
    now = clock.now()
    if not now < inp.preferred_at <= now + MAX_CALLBACK_AHEAD:
        raise ToolDenied("invalid_input", "preferred_at must be in the next 30 days")
    customer_id = task_customer(ctx)
    if customer_id is None:
        raise ToolDenied("not_found", "no customer to schedule a callback for")
    reason = sanitize_text(inp.reason, 200)
    task = await _create_task(ctx, customer_id, "callback", reason, "", inp.preferred_at)
    return TaskCreatedOutput(task_id=task.id, type=task.type, due_at=task.due_at)


@gateway_tool("escalate", scope="any", input_model=EscalateInput, output_model=EscalateOutput)
async def escalate(ctx: ToolContext, inp: EscalateInput) -> EscalateOutput:
    cfg = ctx.gateway.config
    transfer = cfg.transfer_extension if cfg.human_agent_available else None
    customer_id = task_customer(ctx)
    task_id = None
    if customer_id is not None:
        task = await _create_task(
            ctx, customer_id, "escalation", inp.reason.value, sanitize_text(inp.summary, 600), clock.now()
        )
        task_id = task.id
    return EscalateOutput(
        task_id=task_id, human_available=cfg.human_agent_available, transfer_extension=transfer
    )


@gateway_tool(
    "set_contact_flag", scope="verified", input_model=SetContactFlagInput, output_model=ContactFlagOutput
)
async def set_contact_flag(ctx: ToolContext, inp: SetContactFlagInput) -> ContactFlagOutput:
    """Dispute / cease-contact / preferred window. The scheduler re-reads these before every dial."""
    if inp.flag == "dispute":
        patch = c.ContactPreferencesPatch(dispute_open=True)
    elif inp.flag == "cease_contact":
        patch = c.ContactPreferencesPatch(cease_contact=True)
    else:
        patch = c.ContactPreferencesPatch(preferred_window=inp.preferred_window)
    prefs = await ctx.gateway.bank.patch_contact_preferences(ctx.customer_id, patch)
    if prefs is None:
        raise ToolDenied("not_found")
    applied = {
        "dispute": prefs.dispute_open,
        "cease_contact": prefs.cease_contact,
        "preferred_window": prefs.preferred_window == inp.preferred_window,
    }[inp.flag]
    return ContactFlagOutput(flag=inp.flag, applied=applied)


def _merge_sources(*lists: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for items in lists:
        for item in items:
            seen.setdefault(item, None)
    return list(seen)


@gateway_tool(
    "record_call_outcome", scope="worker", input_model=CallOutcome, output_model=OutcomeRecordedOutput
)
async def record_call_outcome(ctx: ToolContext, outcome: CallOutcome) -> OutcomeRecordedOutput:
    """Persist the call's single outcome (PRD §8) and close the session. Worker code only, never the LLM."""
    s = ctx.session
    if outcome.call_id != s.call_id:
        raise ToolDenied("invalid_input", "call_id does not match the session")
    allowed_customers = {None, s.bound_customer_id, s.expected_customer_id}
    if outcome.customer_id not in allowed_customers:
        raise ToolDenied("forbidden", "the outcome names a customer this call was not about")
    if outcome.loan_id not in {None, s.loan_id}:
        owned = s.bound_customer_id is not None and any(
            ln.id == outcome.loan_id for ln in await ctx.gateway.bank.customer_loans(s.bound_customer_id)
        )
        if not owned:
            raise ToolDenied("forbidden", "the outcome names a loan this call was not about")
    final = outcome.model_copy(update={"kb_sources": _merge_sources(outcome.kb_sources, s.kb_sources)})
    await ctx.gateway.bank.upsert_interaction(
        c.InteractionIn(
            call_id=s.call_id,
            customer_id=final.customer_id,
            loan_id=final.loan_id,
            direction=final.direction,
            campaign=final.campaign,
            started_at=s.created_at,
            ended_at=clock.now(),
            contact_result=final.contact_result,
            outcome=final,
            transcript_uri=final.transcript_uri,
            recording_uri=final.recording_uri,
        )
    )
    ctx.gateway.store.close(s.call_id)
    return OutcomeRecordedOutput(interaction_id=s.call_id)
