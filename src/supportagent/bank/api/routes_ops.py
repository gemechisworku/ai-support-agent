"""Operations read endpoints for the console (BANK-R9, T-BANK-07).

Paginated lists return `Page[T]` (`items`, `total`, `limit`, `offset`); `limit` defaults to 50 and
values above 200 are capped at 200. `PATCH /tasks/{id}` is the only write here; it touches the
interaction table `tasks` (the console's follow-up queue marks tasks done or assigns them).
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime

from supportagent.bank import queries
from supportagent.bank.api.deps import SessionDep
from supportagent.contracts import bank as c

router = APIRouter()

Limit = Annotated[int, Query(ge=1, description="page size; values above 200 are capped at 200")]
Offset = Annotated[int, Query(ge=0)]


def _cap(limit: int) -> int:
    return min(limit, queries.MAX_PAGE)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@router.get("/interactions", response_model=c.Page[c.Interaction])
def list_interactions(
    s: SessionDep,
    limit: Limit = 50,
    offset: Offset = 0,
    direction: Literal["inbound", "outbound"] | None = None,
    campaign: str | None = None,
    contact_result: str | None = None,
    escalated: bool | None = None,
    customer_id: int | None = None,
    since: AwareDatetime | None = None,
    until: AwareDatetime | None = None,
) -> c.Page[c.Interaction]:
    return queries.list_interactions(
        s,
        limit=_cap(limit),
        offset=offset,
        direction=direction,
        campaign=campaign,
        contact_result=contact_result,
        escalated=escalated,
        customer_id=customer_id,
        since=since,
        until=until,
    )


@router.get("/interactions/{call_id}", response_model=c.CallDetail)
def call_detail(s: SessionDep, call_id: str) -> c.CallDetail:
    detail = queries.call_detail(s, call_id) if _is_uuid(call_id) else None
    if detail is None:
        raise HTTPException(404, detail=f"call {call_id} not found")
    return detail


@router.get("/tasks", response_model=c.Page[c.Task])
def list_tasks(
    s: SessionDep,
    limit: Limit = 50,
    offset: Offset = 0,
    status: Literal["open", "done", "cancelled"] | None = None,
    type: Literal["callback", "escalation"] | None = None,
    customer_id: int | None = None,
) -> c.Page[c.Task]:
    return queries.list_tasks(
        s, limit=_cap(limit), offset=offset, status=status, task_type=type, customer_id=customer_id
    )


@router.patch("/tasks/{task_id}", response_model=c.Task)
def patch_task(s: SessionDep, task_id: int, body: c.TaskPatch) -> c.Task:
    try:
        return queries.patch_task(s, task_id, body)
    except queries.NotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/promises", response_model=c.Page[c.Promise])
def list_promises(
    s: SessionDep,
    limit: Limit = 50,
    offset: Offset = 0,
    status: Literal["open", "kept", "broken"] | None = None,
    loan_id: int | None = None,
) -> c.Page[c.Promise]:
    return queries.list_promises(s, limit=_cap(limit), offset=offset, status=status, loan_id=loan_id)


@router.get("/audit", response_model=c.Page[c.AuditRow])
def list_audit(
    s: SessionDep,
    limit: Limit = 50,
    offset: Offset = 0,
    call_id: str | None = None,
    tool: str | None = None,
    decision: Literal["allow", "deny", "error"] | None = None,
) -> c.Page[c.AuditRow]:
    return queries.list_audit(
        s, limit=_cap(limit), offset=offset, call_id=call_id, tool=tool, decision=decision
    )


@router.get("/outbox", response_model=c.Page[c.OutboxMessage])
def list_outbox(
    s: SessionDep,
    limit: Limit = 50,
    offset: Offset = 0,
    status: Literal["pending", "sent"] | None = None,
) -> c.Page[c.OutboxMessage]:
    return queries.list_outbox(s, limit=_cap(limit), offset=offset, status=status)
