"""Read and write functions over the bank tables, returning `supportagent.contracts.bank` models.

Routers are thin wrappers around these, so the SQL is testable without HTTP. Derived loan figures
always come from `ledger.py` (BANK-R5).
"""

import datetime as dt
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from supportagent.bank import ledger, models
from supportagent.common import clock
from supportagent.contracts import bank as c


def get_customer(s: Session, customer_id: int) -> c.Customer | None:
    row = s.get(models.Customer, customer_id)
    return c.Customer.model_validate(row) if row else None


def customers_by_phone(s: Session, phone: str) -> list[c.Customer]:
    rows = s.scalars(
        select(models.Customer).where(models.Customer.phone == phone).order_by(models.Customer.id)
    )
    return [c.Customer.model_validate(r) for r in rows]


def customer_loans(s: Session, customer_id: int) -> list[c.Loan]:
    rows = s.scalars(
        select(models.Loan).where(models.Loan.customer_id == customer_id).order_by(models.Loan.id)
    )
    return [c.Loan.model_validate(r) for r in rows]


def account_exists(s: Session, account_id: int) -> bool:
    return s.get(models.Account, account_id) is not None


def _installment_rows(s: Session, loan_id: int) -> list[models.Installment]:
    stmt = (
        select(models.Installment)
        .where(models.Installment.loan_id == loan_id)
        .order_by(models.Installment.seq)
    )
    return list(s.scalars(stmt))


def installments(s: Session, loan_id: int) -> list[c.Installment] | None:
    if s.get(models.Loan, loan_id) is None:
        return None
    return [c.Installment.model_validate(r) for r in _installment_rows(s, loan_id)]


def loan_status(s: Session, loan_id: int, as_of: dt.date) -> c.LoanStatus | None:
    loan = s.get(models.Loan, loan_id)
    if loan is None:
        return None
    product = s.get(models.LoanProduct, loan.product_code)
    assert product is not None  # foreign key
    return _status(loan, product.name, _installment_rows(s, loan_id), as_of)


def transactions(s: Session, account_id: int, limit: int, loan_id: int | None = None) -> list[c.Transaction]:
    stmt = select(models.Transaction).where(models.Transaction.account_id == account_id)
    if loan_id is not None:
        stmt = stmt.where(models.Transaction.loan_id == loan_id)
    stmt = stmt.order_by(models.Transaction.ts.desc(), models.Transaction.id.desc()).limit(limit)
    return [c.Transaction.model_validate(r) for r in s.scalars(stmt)]


def interaction_history(s: Session, customer_id: int, limit: int) -> c.InteractionHistory:
    calls = s.scalars(
        select(models.Interaction)
        .where(models.Interaction.customer_id == customer_id)
        .order_by(models.Interaction.started_at.desc())
        .limit(limit)
    )
    promises = s.scalars(
        select(models.PromiseToPay)
        .join(models.Loan, models.Loan.id == models.PromiseToPay.loan_id)
        .where(models.Loan.customer_id == customer_id, models.PromiseToPay.status == "open")
        .order_by(models.PromiseToPay.promised_date)
    )
    tasks = s.scalars(
        select(models.Task)
        .where(models.Task.customer_id == customer_id, models.Task.status == "open")
        .order_by(models.Task.due_at)
    )
    return c.InteractionHistory(
        interactions=[c.Interaction.model_validate(r) for r in calls],
        open_promises=[c.Promise.model_validate(r) for r in promises],
        open_tasks=[c.Task.model_validate(r) for r in tasks],
    )


# --------------------------------------------------------------------------- eligibility (T-BANK-05)


def eligible_loans(
    s: Session,
    as_of: dt.date,
    now: dt.datetime,
    *,
    bucket: str | None = None,
    due_within: int | None = None,
) -> list[c.EligibleLoan]:
    """Candidate loans with the facts the compliance gate needs. No contact rules are applied here.

    - `bucket="1-30"` (or another delinquency bucket): loans whose DPD at `as_of` falls in the bucket;
      `amount_due` is the overdue amount.
    - `due_within=N`: loans with an unpaid instalment due in [as_of, as_of + N]; `amount_due` is that
      instalment's remaining amount.

    `attempts_last_7d` counts outbound interactions for the loan since `now - 7 days`;
    `last_conversation_at` is the latest right-party interaction for the loan.
    """
    if (bucket is None) == (due_within is None):
        raise ValueError("pass exactly one of bucket or due_within")
    loans = list(
        s.scalars(select(models.Loan).where(models.Loan.status != "closed").order_by(models.Loan.id))
    )
    schedules: dict[int, list[models.Installment]] = {}
    for inst in s.scalars(
        select(models.Installment).order_by(models.Installment.loan_id, models.Installment.seq)
    ):
        schedules.setdefault(inst.loan_id, []).append(inst)

    picked: list[tuple[models.Loan, int, ledger.Bucket, dt.date | None, Decimal]] = []
    for loan in loans:
        insts = schedules.get(loan.id, [])
        dpd = ledger.days_past_due(insts, as_of)
        loan_bucket = ledger.bucket(dpd)
        upcoming = ledger.next_due(insts, as_of)
        if bucket is not None:
            if loan_bucket == bucket:
                next_date = upcoming[0] if upcoming else None
                picked.append((loan, dpd, loan_bucket, next_date, ledger.overdue_amount(insts, as_of)))
        elif (
            upcoming is not None
            and due_within is not None
            and upcoming[0] <= as_of + dt.timedelta(days=due_within)
        ):
            picked.append((loan, dpd, loan_bucket, upcoming[0], upcoming[1]))
    if not picked:
        return []

    ids = [p[0].id for p in picked]
    attempt_rows = s.execute(
        select(models.Interaction.loan_id, func.count())
        .where(
            models.Interaction.loan_id.in_(ids),
            models.Interaction.direction == "outbound",
            models.Interaction.started_at >= now - dt.timedelta(days=7),
        )
        .group_by(models.Interaction.loan_id)
    )
    attempts: dict[int | None, int] = {loan_id: n for loan_id, n in attempt_rows}
    conversation_rows = s.execute(
        select(models.Interaction.loan_id, func.max(models.Interaction.started_at))
        .where(models.Interaction.loan_id.in_(ids), models.Interaction.contact_result == "right_party")
        .group_by(models.Interaction.loan_id)
    )
    conversations: dict[int | None, dt.datetime] = {loan_id: ts for loan_id, ts in conversation_rows}
    customer_ids = {p[0].customer_id for p in picked}
    customers = {
        x.id: x for x in s.scalars(select(models.Customer).where(models.Customer.id.in_(customer_ids)))
    }
    prefs = {
        x.customer_id: x
        for x in s.scalars(
            select(models.ContactPreference).where(models.ContactPreference.customer_id.in_(customer_ids))
        )
    }
    out = []
    for loan, dpd, loan_bucket, next_date, amount in picked:
        customer = customers[loan.customer_id]
        out.append(
            c.EligibleLoan(
                loan_id=loan.id,
                customer_id=loan.customer_id,
                product_code=loan.product_code,
                timezone=customer.timezone,
                phone=customer.phone,
                days_past_due=dpd,
                bucket=loan_bucket,
                next_due_date=next_date,
                amount_due=amount,
                prefs=c.ContactPreferences.model_validate(prefs[loan.customer_id]),
                attempts_last_7d=int(attempts.get(loan.id, 0)),
                last_conversation_at=conversations.get(loan.id),
            )
        )
    return out


# --------------------------------------------------------------------------- writes (T-BANK-06)
# Only interaction tables are written here (BANK-R7, constitution II.3). Business validation of
# promises and tasks is the gateway's job; the bank checks types and that referenced rows exist.


class NotFoundError(LookupError):
    """A referenced customer, loan or preference row does not exist."""


def get_interaction(s: Session, call_id: str) -> c.Interaction | None:
    row = s.scalars(
        select(models.Interaction)
        .where(models.Interaction.call_id == call_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    return c.Interaction.model_validate(row) if row else None


def upsert_interaction(s: Session, data: c.InteractionIn, bank_name: str) -> c.Interaction:
    """Insert or update by `call_id`, and enqueue the post-call summary (F13) in the same savepoint.

    The first write's `started_at` is kept. Nullable fields keep their stored value unless the new
    write sets them, so the scheduler's attempt row and the worker's final outcome merge.
    """
    outcome = data.outcome
    values = {
        "call_id": data.call_id,
        "customer_id": data.customer_id
        if data.customer_id is not None
        else (outcome.customer_id if outcome else None),
        "loan_id": data.loan_id if data.loan_id is not None else (outcome.loan_id if outcome else None),
        "direction": data.direction.value,
        "campaign": data.campaign.value if data.campaign else None,
        "started_at": data.started_at,
        "ended_at": data.ended_at,
        "contact_result": data.contact_result.value,
        "outcome": outcome.model_dump(mode="json") if outcome else None,
        "transcript_uri": data.transcript_uri or (outcome.transcript_uri if outcome else None),
        "recording_uri": data.recording_uri or (outcome.recording_uri if outcome else None),
        "versions": outcome.versions.model_dump(mode="json") if outcome else None,
    }
    table = models.Interaction.__table__
    stmt = pg_insert(models.Interaction).values(**values)
    keep_if_null = (
        "customer_id",
        "loan_id",
        "campaign",
        "ended_at",
        "outcome",
        "transcript_uri",
        "recording_uri",
        "versions",
    )
    update: dict[str, Any] = {k: func.coalesce(stmt.excluded[k], table.c[k]) for k in keep_if_null}
    update["contact_result"] = stmt.excluded.contact_result
    update["direction"] = stmt.excluded.direction
    stmt = stmt.on_conflict_do_update(index_elements=["call_id"], set_=update)
    with s.begin_nested():
        s.execute(stmt)
        if outcome is not None and outcome.summary.strip():
            enqueue_summary(s, data.call_id, values["customer_id"], outcome.summary, bank_name)  # type: ignore[arg-type]
    result = get_interaction(s, data.call_id)
    assert result is not None
    return result


def enqueue_summary(s: Session, call_id: str, customer_id: int | None, summary: str, bank_name: str) -> None:
    """One outbox row per call (SMS if the customer has a phone, else email). Nothing is sent (mock)."""
    if customer_id is None:
        return
    exists = s.scalar(select(func.count()).select_from(models.Outbox).where(models.Outbox.call_id == call_id))
    if exists:
        return
    customer = s.get(models.Customer, customer_id)
    if customer is None or not (customer.phone or customer.email):
        return
    channel, recipient = ("sms", customer.phone) if customer.phone else ("email", customer.email)
    s.add(
        models.Outbox(
            call_id=call_id,
            channel=channel,
            recipient=recipient,
            body=f"{bank_name}: {summary.strip()} Reply or call us if you have questions.",
            status="pending",
        )
    )
    s.flush()


def create_promise(s: Session, data: c.PromiseIn) -> c.Promise:
    if s.get(models.Loan, data.loan_id) is None:
        raise NotFoundError(f"loan {data.loan_id} not found")
    row = models.PromiseToPay(
        call_id=data.call_id,
        loan_id=data.loan_id,
        promised_date=data.promised_date,
        amount=data.amount,
        status="open",
    )
    s.add(row)
    s.flush()
    s.refresh(row)
    return c.Promise.model_validate(row)


def create_task(s: Session, data: c.TaskIn) -> c.Task:
    if s.get(models.Customer, data.customer_id) is None:
        raise NotFoundError(f"customer {data.customer_id} not found")
    if data.loan_id is not None and s.get(models.Loan, data.loan_id) is None:
        raise NotFoundError(f"loan {data.loan_id} not found")
    row = models.Task(**data.model_dump(), status="open")
    s.add(row)
    s.flush()
    s.refresh(row)
    return c.Task.model_validate(row)


def patch_contact_preferences(
    s: Session, customer_id: int, patch: c.ContactPreferencesPatch
) -> c.ContactPreferences:
    row = s.get(models.ContactPreference, customer_id)
    if row is None:
        raise NotFoundError(f"contact preferences for customer {customer_id} not found")
    for key, value in patch.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.updated_at = clock.now()
    s.flush()
    return c.ContactPreferences.model_validate(row)


def append_audit(s: Session, data: c.AuditIn) -> None:
    s.add(models.AuditLog(**data.model_dump()))
    s.flush()


# --------------------------------------------------------------------------- operations read API (T-BANK-07)

MAX_PAGE = 200


def _page[M: BaseModel](s: Session, stmt: Select[Any], model: type[M], limit: int, offset: int) -> c.Page[M]:
    """Run `stmt` (already filtered and ordered) as one page, with the unpaginated total."""
    total = s.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = [model.model_validate(r) for r in s.scalars(stmt.limit(limit).offset(offset))]
    return c.Page[model](items=items, total=total, limit=limit, offset=offset)  # type: ignore[valid-type]


def list_interactions(
    s: Session,
    *,
    limit: int,
    offset: int,
    direction: str | None = None,
    campaign: str | None = None,
    contact_result: str | None = None,
    escalated: bool | None = None,
    customer_id: int | None = None,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> c.Page[c.Interaction]:
    """Calls, newest first. Filters combine with AND; `escalated` reads the stored outcome."""
    i = models.Interaction
    stmt = select(i)
    if direction is not None:
        stmt = stmt.where(i.direction == direction)
    if campaign is not None:
        stmt = stmt.where(i.campaign == campaign)
    if contact_result is not None:
        stmt = stmt.where(i.contact_result == contact_result)
    if customer_id is not None:
        stmt = stmt.where(i.customer_id == customer_id)
    if since is not None:
        stmt = stmt.where(i.started_at >= since)
    if until is not None:
        stmt = stmt.where(i.started_at < until)
    if escalated is not None:
        is_escalated = func.coalesce(func.jsonb_typeof(i.outcome["escalated"]), "null") == "object"
        stmt = stmt.where(is_escalated if escalated else ~is_escalated)
    return _page(s, stmt.order_by(i.started_at.desc(), i.call_id), c.Interaction, limit, offset)


def call_detail(s: Session, call_id: str) -> c.CallDetail | None:
    interaction = get_interaction(s, call_id)
    if interaction is None:
        return None
    customer = s.get(models.Customer, interaction.customer_id) if interaction.customer_id else None
    p, t, a, o = models.PromiseToPay, models.Task, models.AuditLog, models.Outbox
    promises = s.scalars(select(p).where(p.call_id == call_id).order_by(p.id))
    tasks = s.scalars(select(t).where(t.call_id == call_id).order_by(t.id))
    audit = s.scalars(select(a).where(a.call_id == call_id).order_by(a.ts, a.id))
    outbox = s.scalars(select(o).where(o.call_id == call_id).order_by(o.id))
    return c.CallDetail(
        interaction=interaction,
        customer_timezone=customer.timezone if customer else None,
        promises=[c.Promise.model_validate(r) for r in promises],
        tasks=[c.Task.model_validate(r) for r in tasks],
        audit=[c.AuditRow.model_validate(r) for r in audit],
        outbox=[c.OutboxMessage.model_validate(r) for r in outbox],
    )


def list_tasks(
    s: Session,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
    task_type: str | None = None,
    customer_id: int | None = None,
) -> c.Page[c.Task]:
    """Callbacks and escalations, soonest due first."""
    t = models.Task
    stmt = select(t)
    if status is not None:
        stmt = stmt.where(t.status == status)
    if task_type is not None:
        stmt = stmt.where(t.type == task_type)
    if customer_id is not None:
        stmt = stmt.where(t.customer_id == customer_id)
    return _page(s, stmt.order_by(t.due_at, t.id), c.Task, limit, offset)


def list_promises(
    s: Session, *, limit: int, offset: int, status: str | None = None, loan_id: int | None = None
) -> c.Page[c.Promise]:
    """Promises to pay, earliest promised date first."""
    p = models.PromiseToPay
    stmt = select(p)
    if status is not None:
        stmt = stmt.where(p.status == status)
    if loan_id is not None:
        stmt = stmt.where(p.loan_id == loan_id)
    return _page(s, stmt.order_by(p.promised_date, p.id), c.Promise, limit, offset)


def list_audit(
    s: Session,
    *,
    limit: int,
    offset: int,
    call_id: str | None = None,
    tool: str | None = None,
    decision: str | None = None,
) -> c.Page[c.AuditRow]:
    """Audit rows oldest first, so a call's trail reads top to bottom."""
    a = models.AuditLog
    stmt = select(a)
    if call_id is not None:
        stmt = stmt.where(a.call_id == call_id)
    if tool is not None:
        stmt = stmt.where(a.tool == tool)
    if decision is not None:
        stmt = stmt.where(a.decision == decision)
    return _page(s, stmt.order_by(a.ts, a.id), c.AuditRow, limit, offset)


def list_outbox(s: Session, *, limit: int, offset: int, status: str | None = None) -> c.Page[c.OutboxMessage]:
    """Post-call summaries, newest first."""
    o = models.Outbox
    stmt = select(o)
    if status is not None:
        stmt = stmt.where(o.status == status)
    return _page(s, stmt.order_by(o.created_at.desc(), o.id.desc()), c.OutboxMessage, limit, offset)


# --------------------------------------------------------------------------- explorer (T-BANK-09)

BUCKET_ORDER: dict[str, int] = {"current": 0, "1-30": 1, "31-60": 2, "61-90": 3, "90+": 4}


def _schedules(s: Session, loan_ids: list[int] | None = None) -> dict[int, list[models.Installment]]:
    stmt = select(models.Installment).order_by(models.Installment.loan_id, models.Installment.seq)
    if loan_ids is not None:
        stmt = stmt.where(models.Installment.loan_id.in_(loan_ids))
    out: dict[int, list[models.Installment]] = {}
    for inst in s.scalars(stmt):
        out.setdefault(inst.loan_id, []).append(inst)
    return out


def _status(
    loan: models.Loan, product_name: str, insts: list[models.Installment], as_of: dt.date
) -> c.LoanStatus:
    return c.LoanStatus.model_validate(
        {
            "loan_id": loan.id,
            "customer_id": loan.customer_id,
            "number_last4": loan.number[-4:],
            "product_code": loan.product_code,
            "product_name": product_name,
            "as_of": as_of,
            "status": loan.status,
            **ledger.summary(insts, as_of),
        }
    )


def search_customers(
    s: Session,
    as_of: dt.date,
    *,
    limit: int,
    offset: int,
    q: str | None = None,
    segment: str | None = None,
    bucket: str | None = None,
) -> c.Page[c.CustomerSummary]:
    """Customers matching `q` (name, phone, customer id or loan last 4), ordered by id.

    `worst_bucket` is computed from each customer's loans at `as_of` by the ledger, so the `bucket`
    filter is applied after that (the population is small: about 200 customers).
    """
    cu, lo = models.Customer, models.Loan
    stmt = select(cu)
    if segment is not None:
        stmt = stmt.where(cu.segment == segment)
    term = (q or "").strip()
    if term:
        conditions: list[Any] = [cu.full_name.ilike(f"%{term}%"), cu.phone.contains(term)]
        if term.isdigit():
            conditions.append(cu.id == int(term))
            if len(term) == 4:
                last4 = select(lo.customer_id).where(func.right(lo.number, 4) == term)
                conditions.append(cu.id.in_(last4))
        stmt = stmt.where(or_(*conditions))
    customers = list(s.scalars(stmt.order_by(cu.id)))
    ids = [x.id for x in customers]
    loans_by_customer: dict[int, list[models.Loan]] = {}
    for loan in s.scalars(select(lo).where(lo.customer_id.in_(ids)).order_by(lo.id)):
        loans_by_customer.setdefault(loan.customer_id, []).append(loan)
    schedules = _schedules(s, [loan.id for loans in loans_by_customer.values() for loan in loans])
    rows: list[c.CustomerSummary] = []
    for customer in customers:
        loans = loans_by_customer.get(customer.id, [])
        buckets: list[ledger.Bucket] = [
            ledger.bucket(ledger.days_past_due(schedules.get(x.id, []), as_of)) for x in loans
        ]
        ranked = sorted(buckets, key=BUCKET_ORDER.__getitem__)
        worst = ranked[-1] if ranked else None
        if bucket is not None and worst != bucket:
            continue
        rows.append(
            c.CustomerSummary(
                customer_id=customer.id,
                full_name=customer.full_name,
                phone=customer.phone,
                segment=customer.segment,
                loan_count=len(loans),
                worst_bucket=worst,
            )
        )
    return c.Page[c.CustomerSummary](
        items=rows[offset : offset + limit], total=len(rows), limit=limit, offset=offset
    )


def list_loans(
    s: Session,
    as_of: dt.date,
    *,
    limit: int,
    offset: int,
    bucket: str | None = None,
    product: str | None = None,
) -> c.Page[c.LoanStatus]:
    """Loans with their computed status at `as_of`, ordered by loan id."""
    stmt = select(models.Loan)
    if product is not None:
        stmt = stmt.where(models.Loan.product_code == product)
    loans = list(s.scalars(stmt.order_by(models.Loan.id)))
    names = {p.code: p.name for p in s.scalars(select(models.LoanProduct))}
    schedules = _schedules(s, [x.id for x in loans]) if loans else {}
    statuses = [_status(x, names[x.product_code], schedules.get(x.id, []), as_of) for x in loans]
    if bucket is not None:
        statuses = [x for x in statuses if x.bucket == bucket]
    return c.Page[c.LoanStatus](
        items=statuses[offset : offset + limit], total=len(statuses), limit=limit, offset=offset
    )


def match_customers_stmt(dob: dt.date, loan_last4: str) -> Select[int, int]:
    """Loans of customers born on `dob` whose number ends in `loan_last4` (uses ix_customers_dob)."""
    cu, lo = models.Customer, models.Loan
    return (
        select(lo.customer_id, lo.id)
        .join(cu, cu.id == lo.customer_id)
        .where(cu.dob == dob, func.right(lo.number, 4) == loan_last4)
        .order_by(lo.customer_id, lo.id)
    )


def match_customers(s: Session, dob: dt.date, loan_last4: str) -> list[c.CustomerMatch]:
    """Identity candidates for an inbound caller whose phone number is not on file (T-BANK-08)."""
    rows = s.execute(match_customers_stmt(dob, loan_last4))
    return [c.CustomerMatch(customer_id=customer_id, loan_id=loan_id) for customer_id, loan_id in rows]


def contact_preferences(s: Session, customer_id: int) -> c.ContactPreferences | None:
    row = s.get(models.ContactPreference, customer_id)
    return c.ContactPreferences.model_validate(row) if row else None


def patch_task(s: Session, task_id: int, patch: c.TaskPatch) -> c.Task:
    row = s.get(models.Task, task_id)
    if row is None:
        raise NotFoundError(f"task {task_id} not found")
    for key, value in patch.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    s.flush()
    return c.Task.model_validate(row)
