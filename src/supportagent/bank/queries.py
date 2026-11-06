"""Read and write functions over the bank tables, returning `supportagent.contracts.bank` models.

Routers are thin wrappers around these, so the SQL is testable without HTTP. Derived loan figures
always come from `ledger.py` (BANK-R5).
"""

import datetime as dt

from sqlalchemy import select
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
