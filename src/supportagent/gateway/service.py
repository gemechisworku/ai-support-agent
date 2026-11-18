"""The scheduler's service-scope API (GW-R11, T-GW-10).

`Authorization: Bearer $GATEWAY_SERVICE_TOKEN`; a worker or session token is 403 and the service token cannot
call MCP tools. Every request is audited with `call_id="service"` before the response (GW-R8, `auth.py`).

- `GET  /service/candidates?campaign=pre_due|missed_payment|callback` → `list[Candidate]`
    - `pre_due`: an unpaid instalment due within 3 days (`GET /collections/eligible?due_within=3`)
    - `missed_payment`: 1 to 30 days past due (`?bucket=1-30`)
    - `callback`: open callback tasks that are due, and broken promises
  Each row carries the facts the compliance gate needs (contact preferences, timezone, `attempts_last_7d`,
  `last_conversation_at`). Nothing is filtered on contact rules here: a customer with `cease_contact` is still
  listed, because applying the rules is the scheduler's job (COMP), so that the decision is made in one place.
- `GET  /service/contact-preferences/{customer_id}` → `ContactPrefs`: the current values, which the scheduler
  re-reads just before dialling
- `POST /service/attempts` (`CallAttempt`) → `AttemptRecorded`: writes an outbound interaction row keyed by
  `call_id` (the worker later completes it with the outcome)
"""

import datetime as dt

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import TypeAdapter, ValidationError

from supportagent.common import clock
from supportagent.contracts import bank as b
from supportagent.contracts.gateway import AttemptRecorded, CallAttempt, Candidate, ContactPrefs
from supportagent.contracts.money import Money
from supportagent.contracts.outcome import Campaign
from supportagent.gateway.auth import gateway_of, service_call
from supportagent.gateway.bank_client import BankClient

router = APIRouter(prefix="/service", tags=["service"])

PRE_DUE_DAYS = 3
MISSED_BUCKET = "1-30"
CANDIDATE_CAMPAIGNS = ("pre_due", "missed_payment", "callback")
HISTORY_LIMIT = 50


def _prefs(p: b.ContactPreferences) -> ContactPrefs:
    return ContactPrefs(
        consent_voice_ai=p.consent_voice_ai,
        dnc=p.dnc,
        cease_contact=p.cease_contact,
        dispute_open=p.dispute_open,
        preferred_window=p.preferred_window,
    )


def _from_eligible(row: b.EligibleLoan, campaign: Campaign) -> Candidate:
    return Candidate(
        customer_id=row.customer_id,
        loan_id=row.loan_id,
        phone=row.phone,
        timezone=row.timezone,
        campaign=campaign,
        due_date=row.next_due_date,
        amount_due=row.amount_due,
        days_past_due=row.days_past_due,
        contact=_prefs(row.prefs),
        attempts_last_7d=row.attempts_last_7d,
        last_conversation_at=row.last_conversation_at,
    )


async def _callback_loan(bank: BankClient, customer_id: int, loan_id: int | None) -> int | None:
    if loan_id is not None:
        return loan_id
    loans = [ln for ln in await bank.customer_loans(customer_id) if ln.status != "closed"]
    return min(loans, key=lambda ln: ln.id).id if loans else None


async def _callback_candidate(
    bank: BankClient,
    now: dt.datetime,
    *,
    customer_id: int,
    loan_id: int | None,
    task_id: int | None,
    due_date: dt.date | None = None,
    amount_due: Money | None = None,
) -> Candidate | None:
    """The facts for one callback or broken promise. `None` if the customer, loan or preferences are gone."""
    loan_id = await _callback_loan(bank, customer_id, loan_id)
    if loan_id is None:
        return None
    customer = await bank.get_customer(customer_id)
    prefs = await bank.contact_preferences(customer_id)
    loan = await bank.loan_status(loan_id)
    if customer is None or prefs is None or loan is None or loan.customer_id != customer_id:
        return None
    history = await bank.interaction_history(customer_id, limit=HISTORY_LIMIT)
    mine = [i for i in history.interactions if i.loan_id == loan_id]
    talked = [i.started_at for i in mine if i.contact_result == "right_party"]
    return Candidate(
        customer_id=customer_id,
        loan_id=loan_id,
        phone=customer.phone,
        timezone=customer.timezone,
        campaign="callback",
        due_date=due_date or loan.next_due_date,
        amount_due=amount_due
        if amount_due is not None
        else (loan.overdue_amount if loan.days_past_due else loan.next_due_amount),
        days_past_due=loan.days_past_due,
        contact=_prefs(prefs),
        attempts_last_7d=sum(
            1 for i in mine if i.direction == "outbound" and i.started_at >= now - dt.timedelta(days=7)
        ),
        last_conversation_at=max(talked) if talked else None,
        task_id=task_id,
    )


async def candidates_for(bank: BankClient, campaign: Campaign) -> list[Candidate]:
    if campaign == "pre_due":
        return [_from_eligible(r, campaign) for r in await bank.eligible_loans(due_within=PRE_DUE_DAYS)]
    if campaign == "missed_payment":
        return [_from_eligible(r, campaign) for r in await bank.eligible_loans(bucket=MISSED_BUCKET)]
    now = clock.now()
    out: list[Candidate] = []
    for task in await bank.list_tasks(status="open", task_type="callback"):
        if task.due_at <= now:
            cand = await _callback_candidate(
                bank, now, customer_id=task.customer_id, loan_id=task.loan_id, task_id=task.id
            )
            if cand is not None:
                out.append(cand)
    for promise in await bank.list_promises(status="broken"):
        loan = await bank.loan_status(promise.loan_id)
        if loan is None:
            continue
        cand = await _callback_candidate(
            bank,
            now,
            customer_id=loan.customer_id,
            loan_id=promise.loan_id,
            task_id=None,
            due_date=promise.promised_date,
            amount_due=promise.amount,
        )
        if cand is not None:
            out.append(cand)
    return sorted(out, key=lambda c: (c.customer_id, c.loan_id, c.task_id or 0))


# Query and body are validated inside `service_call`, after the credential check, so an unauthenticated or
# wrong-scope request is always 401/403 (and audited) and never a 422 that reveals the schema.


@router.get("/candidates", response_model=list[Candidate])
async def candidates(request: Request, campaign: str | None = None) -> list[Candidate]:
    bank = gateway_of(request).bank

    async def produce() -> list[Candidate]:
        if campaign not in CANDIDATE_CAMPAIGNS:  # also `inbound`, `playground`: there is nobody to call
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"campaign must be one of {CANDIDATE_CAMPAIGNS}"
            )
        return await candidates_for(bank, TypeAdapter(Campaign).validate_python(campaign))

    return await service_call(request, "service.candidates", {"campaign": campaign}, produce)


@router.get("/contact-preferences/{customer_id}", response_model=ContactPrefs)
async def contact_preferences(request: Request, customer_id: int) -> ContactPrefs:
    bank = gateway_of(request).bank

    async def produce() -> ContactPrefs:
        prefs = await bank.contact_preferences(customer_id)
        if prefs is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="customer not found")
        return _prefs(prefs)

    return await service_call(request, "service.contact_preferences", {"customer_id": customer_id}, produce)


@router.post(
    "/attempts",
    response_model=AttemptRecorded,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": CallAttempt.model_json_schema()}},
        }
    },
)
async def record_attempt(request: Request) -> AttemptRecorded:
    bank = gateway_of(request).bank
    raw: object = None

    async def produce() -> AttemptRecorded:
        try:
            attempt = CallAttempt.model_validate(raw)
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=[e["msg"] for e in exc.errors()]
            ) from exc
        loan = await bank.loan_status(attempt.loan_id)
        if loan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="loan not found")
        if loan.customer_id != attempt.customer_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail="loan does not belong to customer"
            )
        row = await bank.upsert_interaction(
            b.InteractionIn(
                call_id=attempt.call_id,
                customer_id=attempt.customer_id,
                loan_id=attempt.loan_id,
                direction="outbound",
                campaign=attempt.campaign,
                started_at=attempt.started_at,
                contact_result=attempt.contact_result,
            )
        )
        return AttemptRecorded(interaction_id=row.call_id)

    try:
        raw = await request.json()
    except ValueError:
        raw = None
    return await service_call(request, "service.record_attempt", raw, produce)
