"""Read endpoints for customers, loans and accounts (BANK-R4, BANK-R5)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from supportagent.bank import queries
from supportagent.bank.api.deps import AsOfDep, SessionDep
from supportagent.contracts import bank as c

router = APIRouter()
E164 = r"^\+[1-9]\d{6,14}$"
MAX_TRANSACTIONS = 50


def _not_found(what: str, id_: int) -> HTTPException:
    return HTTPException(404, detail=f"{what} {id_} not found")


@router.get("/customers", response_model=list[c.Customer])
def customers_by_phone(s: SessionDep, phone: Annotated[str, Query(pattern=E164)]) -> list[c.Customer]:
    return queries.customers_by_phone(s, phone)


@router.get("/customers/{customer_id}", response_model=c.Customer)
def get_customer(s: SessionDep, customer_id: int) -> c.Customer:
    customer = queries.get_customer(s, customer_id)
    if customer is None:
        raise _not_found("customer", customer_id)
    return customer


@router.get("/customers/{customer_id}/loans", response_model=list[c.Loan])
def customer_loans(s: SessionDep, customer_id: int) -> list[c.Loan]:
    if queries.get_customer(s, customer_id) is None:
        raise _not_found("customer", customer_id)
    return queries.customer_loans(s, customer_id)


@router.get("/customers/{customer_id}/interactions", response_model=c.InteractionHistory)
def customer_interactions(
    s: SessionDep, customer_id: int, limit: Annotated[int, Query(ge=1)] = 10
) -> c.InteractionHistory:
    if queries.get_customer(s, customer_id) is None:
        raise _not_found("customer", customer_id)
    return queries.interaction_history(s, customer_id, min(limit, MAX_TRANSACTIONS))


@router.get("/loans/{loan_id}", response_model=c.LoanStatus)
def loan_status(s: SessionDep, as_of: AsOfDep, loan_id: int) -> c.LoanStatus:
    status = queries.loan_status(s, loan_id, as_of)
    if status is None:
        raise _not_found("loan", loan_id)
    return status


@router.get("/loans/{loan_id}/installments", response_model=list[c.Installment])
def loan_installments(s: SessionDep, loan_id: int) -> list[c.Installment]:
    rows = queries.installments(s, loan_id)
    if rows is None:
        raise _not_found("loan", loan_id)
    return rows


@router.get("/accounts/{account_id}/transactions", response_model=list[c.Transaction])
def account_transactions(
    s: SessionDep,
    account_id: int,
    limit: Annotated[int, Query(ge=1)] = 5,
    loan_id: int | None = None,
) -> list[c.Transaction]:
    if not queries.account_exists(s, account_id):
        raise _not_found("account", account_id)
    return queries.transactions(s, account_id, min(limit, MAX_TRANSACTIONS), loan_id)
