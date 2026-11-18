"""Scheduler contracts (SCHED-R1, SCHED-R2): what a campaign run queues and what it excludes.

The candidates themselves are `contracts.gateway.Candidate` (the gateway's service-scope response).
A `CallJob` is a candidate that passed the compliance gate; an `Exclusion` is one that did not, with every
failing reason.
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from supportagent.contracts.outcome import CallId

CampaignName = Literal["pre_due", "missed_payment", "callback"]
"""The three outbound campaigns (PRD §7); `inbound` and `playground` calls are never scheduled."""

JobStatus = Literal[
    "queued", "dialing", "in_call", "completed", "retry_scheduled", "cancelled_by_gate", "failed"
]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CallJob(_Contract):
    job_id: str
    call_id: CallId  # a UUID4 created at queue time; the interaction row and the room are keyed by it
    campaign: CampaignName
    customer_id: int
    loan_id: int
    not_before: AwareDatetime
    attempt_no: int = Field(ge=1)
    status: JobStatus
    reasons: list[str] = Field(default_factory=list)  # compliance reason codes, when the gate cancelled it


class Exclusion(_Contract):
    """A candidate the gate refused. `reasons` lists every failing rule, in the gate's fixed order."""

    customer_id: int
    loan_id: int
    campaign: CampaignName
    reasons: list[str] = Field(min_length=1)
    at: AwareDatetime


class RunReport(_Contract):
    """The result of `run_campaign`: what was (or, for a dry run, would be) queued, and what was excluded."""

    campaign: CampaignName
    dry_run: bool
    run_at: AwareDatetime
    campaigns_version: str  # `campaigns@<version>`
    rules_version: str  # `contact_rules@<version>`
    selected: int  # candidates inside the campaign's selector
    not_selected: int  # candidates the gateway returned outside it (for example 45 days past due)
    queued: list[CallJob]
    exclusions: list[Exclusion]
    already_open: int  # candidates skipped because a job for the same customer, loan and campaign is open
