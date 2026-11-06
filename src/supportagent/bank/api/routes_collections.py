"""Collections and pre-due eligibility (BANK-R6). The bank supplies facts; COMP applies contact rules."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from supportagent.bank import queries
from supportagent.bank.api.deps import AsOfDep, SessionDep
from supportagent.common import clock
from supportagent.contracts import bank as c

router = APIRouter()


@router.get("/collections/eligible", response_model=list[c.EligibleLoan])
def eligible(
    s: SessionDep,
    as_of: AsOfDep,
    bucket: Literal["1-30", "31-60", "61-90", "90+"] | None = None,
    due_within: Annotated[int | None, Query(ge=0, le=31)] = None,
) -> list[c.EligibleLoan]:
    if (bucket is None) == (due_within is None):
        raise HTTPException(422, detail="pass exactly one of bucket or due_within")
    return queries.eligible_loans(s, as_of, clock.now(), bucket=bucket, due_within=due_within)
