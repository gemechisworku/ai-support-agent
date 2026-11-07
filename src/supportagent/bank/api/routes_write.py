"""Write endpoints — interaction tables only (BANK-R7, BANK-R8).

There is deliberately no POST/PUT/PATCH/DELETE on accounts, loans, installments or transactions;
tests/bank/test_routes_readonly_ledger.py inspects the route table to keep it that way.
"""

from fastapi import APIRouter, HTTPException, Response, status

from supportagent.bank import queries
from supportagent.bank.api.deps import SessionDep
from supportagent.common.settings import get_settings
from supportagent.contracts import bank as c

router = APIRouter()


@router.post("/interactions", response_model=c.Interaction)
def upsert_interaction(s: SessionDep, body: c.InteractionIn) -> c.Interaction:
    return queries.upsert_interaction(s, body, get_settings().persona.bank_name)


@router.post("/promises", response_model=c.Promise, status_code=status.HTTP_201_CREATED)
def create_promise(s: SessionDep, body: c.PromiseIn) -> c.Promise:
    try:
        return queries.create_promise(s, body)
    except queries.NotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/tasks", response_model=c.Task, status_code=status.HTTP_201_CREATED)
def create_task(s: SessionDep, body: c.TaskIn) -> c.Task:
    try:
        return queries.create_task(s, body)
    except queries.NotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.patch("/contact-preferences/{customer_id}", response_model=c.ContactPreferences)
def patch_contact_preferences(
    s: SessionDep, customer_id: int, body: c.ContactPreferencesPatch
) -> c.ContactPreferences:
    try:
        return queries.patch_contact_preferences(s, customer_id, body)
    except queries.NotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/audit", status_code=status.HTTP_204_NO_CONTENT)
def append_audit(s: SessionDep, body: c.AuditIn) -> Response:
    queries.append_audit(s, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
