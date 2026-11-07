"""Read-only explorer endpoints for the console's data explorer (BANK-R11, T-BANK-09).

This router is included before `routes_read`, so `/customers/search` is matched before
`/customers/{customer_id}`. Customer search lives at `/customers/search` (not `/customers?q=`)
because `GET /customers?phone=` already returns `list[Customer]` to the gateway.
"""

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from supportagent.bank import queries
from supportagent.bank.api.deps import AsOfDep, SessionDep
from supportagent.bank.seed.personas import PERSONAS
from supportagent.contracts import bank as c

router = APIRouter(tags=["explorer"])

Limit = Annotated[int, Query(ge=1, description="page size; values above 200 are capped at 200")]
Offset = Annotated[int, Query(ge=0)]
BucketQ = Literal["current", "1-30", "31-60", "61-90", "90+"]
ProductQ = Literal["PERSONAL", "AUTO", "MORTGAGE", "SME_WC", "SALARY_ADV"]


@router.get("/customers/search", response_model=c.Page[c.CustomerSummary])
def search_customers(
    s: SessionDep,
    as_of: AsOfDep,
    q: Annotated[
        str | None, Query(max_length=100, description="name, phone, customer id or loan last 4")
    ] = None,
    segment: str | None = None,
    bucket: BucketQ | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> c.Page[c.CustomerSummary]:
    return queries.search_customers(
        s, as_of, limit=min(limit, queries.MAX_PAGE), offset=offset, q=q, segment=segment, bucket=bucket
    )


@router.get("/customers/match", response_model=list[c.CustomerMatch], tags=["identity"])
def match_customers(
    s: SessionDep,
    dob: dt.date,
    loan_last4: Annotated[str, Query(pattern=r"^\d{4}$")],
) -> list[c.CustomerMatch]:
    """Identity candidates for an inbound caller whose number is not on file (T-BANK-08).

    Returns ids only; the gateway still checks the caller's name and applies the 2-failure lock.
    """
    return queries.match_customers(s, dob, loan_last4)


@router.get("/customers/{customer_id}/contact-preferences", response_model=c.ContactPreferences)
def contact_preferences(s: SessionDep, customer_id: int) -> c.ContactPreferences:
    prefs = queries.contact_preferences(s, customer_id)
    if prefs is None:
        raise HTTPException(404, detail=f"customer {customer_id} not found")
    return prefs


@router.get("/loans", response_model=c.Page[c.LoanStatus])
def list_loans(
    s: SessionDep,
    as_of: AsOfDep,
    bucket: BucketQ | None = None,
    product: ProductQ | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> c.Page[c.LoanStatus]:
    return queries.list_loans(
        s, as_of, limit=min(limit, queries.MAX_PAGE), offset=offset, bucket=bucket, product=product
    )


@router.get("/personas", response_model=list[c.PersonaCard])
def personas() -> list[c.PersonaCard]:
    """The 12 scripted personas with their **synthetic test identities** (for role-play testing)."""
    return [
        c.PersonaCard(
            customer_id=p.customer_id,
            full_name=p.name,
            phone=p.phone,
            extension=p.extension,
            dob=p.dob,
            loan_id=p.loan_id,
            loan_last4=p.loan_last4,
            product_code=p.product_code,
            situation=p.situation,
            expected_outcome=p.expected_outcome,
        )
        for p in PERSONAS
    ]
