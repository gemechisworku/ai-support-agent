"""The compliance-gate adapter (SCHED-R2): a gateway `Candidate` in, a queued `CallJob` or an `Exclusion` out.

Every candidate goes through `supportagent.compliance.evaluate`, the single place the contact rules are
applied. All the rules run (no short-circuit), so an exclusion lists every failing reason. The decision is
made at the time the call would be placed (`now`), in the customer's own time zone; a naive `now` is
refused.
"""

import datetime as dt
import uuid
from collections.abc import Callable

from supportagent.compliance.rules import evaluate
from supportagent.contracts.compliance import ContactFacts, ContactRules
from supportagent.contracts.gateway import Candidate
from supportagent.contracts.scheduler import CallJob, CampaignName, Exclusion


def new_uuid() -> str:
    return str(uuid.uuid4())


def facts_from_candidate(candidate: Candidate, planned_at: dt.datetime) -> ContactFacts:
    """The gate's inputs for one candidate, as the gateway reported them."""
    contact = candidate.contact
    return ContactFacts(
        loan_id=candidate.loan_id,
        customer_id=candidate.customer_id,
        timezone=candidate.timezone,
        preferred_window=contact.preferred_window,
        dnc=contact.dnc,
        consent_voice_ai=contact.consent_voice_ai,
        cease_contact=contact.cease_contact,
        dispute_open=contact.dispute_open,
        attempts_last_7d=candidate.attempts_last_7d,
        last_conversation_at=candidate.last_conversation_at,
        planned_at=planned_at,
    )


def admit(
    candidate: Candidate,
    now: dt.datetime,
    rules: ContactRules,
    *,
    new_id: Callable[[], str] = new_uuid,
) -> CallJob | Exclusion:
    """Gate one candidate at `now`. Allowed: a `queued` first-attempt job due now, with a fresh `call_id`.
    Refused: an `Exclusion` carrying every reason code."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    campaign: CampaignName = str(candidate.campaign)  # type: ignore[assignment]  # `Campaign` is a StrEnum
    decision = evaluate(facts_from_candidate(candidate, now), now, rules)
    if decision.allowed:
        return CallJob(
            job_id=new_id(),
            call_id=new_id(),
            campaign=campaign,
            customer_id=candidate.customer_id,
            loan_id=candidate.loan_id,
            not_before=now,
            attempt_no=1,
            status="queued",
            reasons=[],
        )
    return Exclusion(
        customer_id=candidate.customer_id,
        loan_id=candidate.loan_id,
        campaign=campaign,
        reasons=[str(r) for r in decision.reasons],
        at=now,
    )
