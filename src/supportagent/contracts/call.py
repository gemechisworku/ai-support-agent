"""What a call is about when the agent worker starts it (specs/06-agent, owned by T-AGENT-01).

The dispatcher (scheduler, outbound) puts a `CallMetadata` JSON in the LiveKit room metadata; for inbound
SIP calls the worker builds it from the SIP attributes. TEL and SCHED reuse this model.
"""

from pydantic import BaseModel, ConfigDict

from supportagent.contracts.bank import Phone
from supportagent.contracts.outcome import CallId, Campaign, Direction


class CallMetadata(BaseModel):
    """Context of one call. `customer_id` is only a hint for logging/outcomes: the gateway binds identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: CallId
    direction: Direction
    campaign: Campaign
    customer_id: int | None = None
    loan_id: int | None = None  # outbound: the loan the call is about
    callback_task_id: int | None = None  # campaign=callback: the task being worked
    caller_phone: Phone | None = None  # inbound SIP: the calling number (a hint for the gateway)
