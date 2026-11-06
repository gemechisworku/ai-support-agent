"""Read and write functions over the bank tables, returning `supportagent.contracts.bank` models.

Routers are thin wrappers around these, so the SQL is testable without HTTP. Derived loan figures
always come from `ledger.py` (BANK-R5).
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from supportagent.bank import ledger, models
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
    figures = ledger.summary(_installment_rows(s, loan_id), as_of)
    return c.LoanStatus.model_validate(
        {
            "loan_id": loan.id,
            "customer_id": loan.customer_id,
            "number_last4": loan.number[-4:],
            "product_code": loan.product_code,
            "product_name": product.name,
            "as_of": as_of,
            "status": loan.status,
            **figures,
        }
    )


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
