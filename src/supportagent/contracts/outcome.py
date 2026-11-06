"""The structured outcome every call leaves behind (PRD §8, D-OUTCOME, F7) and the shared enums.

Written once per call by the agent worker through the gateway's `record_call_outcome` tool,
stored by the bank as `interactions.outcome`, read by the console, compliance checks and evals.

Additions to PRD §8 (documented in specs/01-platform/plan.md):
- `ContactResult.busy`, because F3 requires detecting busy lines;
- `Campaign.playground`, for no-telephony development calls;
- `latency_stages` (per-stage p50/p90) next to the PRD's end-to-end `latency_p50`/`latency_p90`;
- `transcript_uri`, so a call can be reconstructed (NFR-AUDIT);
- `Sentiment.unknown`, the default when sentiment was not classified (e.g. no answer).
"""

import datetime as dt
import uuid
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from supportagent.contracts.money import Money


def _uuid4(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("call_id must be a UUID4 string") from exc
    if parsed.version != 4:
        raise ValueError("call_id must be a UUID4 string")
    return str(parsed)


CallId = Annotated[str, AfterValidator(_uuid4)]
"""A call's id: a UUID4 string, created by the dispatcher (scheduler) or the worker (inbound)."""


class Direction(StrEnum):
    inbound = "inbound"
    outbound = "outbound"


class Campaign(StrEnum):
    pre_due = "pre_due"  # U1: instalment due within 3 days
    missed_payment = "missed_payment"  # U2: 1-30 days past due
    callback = "callback"  # U5: scheduled callback or broken-promise follow-up
    inbound = "inbound"  # U3/U4: customer called the support line
    playground = "playground"  # development call without telephony


class ContactResult(StrEnum):
    right_party = "right_party"
    third_party = "third_party"
    no_answer = "no_answer"
    voicemail = "voicemail"
    wrong_number = "wrong_number"
    busy = "busy"


class ReasonCode(StrEnum):
    """Why a payment was missed (PRD §7 Discover)."""

    forgot = "forgot"
    insufficient_funds = "insufficient_funds"
    income_disruption = "income_disruption"
    dispute = "dispute"
    other = "other"


class Sentiment(StrEnum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"
    unknown = "unknown"


class Intent(StrEnum):
    """What the caller wanted or did on the call; a call can have several."""

    account_question = "account_question"  # U3
    policy_question = "policy_question"  # U4
    intent_to_pay = "intent_to_pay"  # U1
    promise_to_pay = "promise_to_pay"  # U2
    already_paid = "already_paid"
    callback_request = "callback_request"  # U5
    human_request = "human_request"  # U6
    hardship = "hardship"
    dispute = "dispute"
    complaint = "complaint"
    cease_contact = "cease_contact"
    legal = "legal"  # lawyer or bankruptcy mentioned
    other = "other"


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PromiseToPay(_Contract):
    date: dt.date
    amount: Money = Field(gt=0)


class Callback(_Contract):
    datetime: dt.datetime

    @model_validator(mode="after")
    def _aware(self) -> Self:
        if self.datetime.tzinfo is None or self.datetime.utcoffset() is None:
            raise ValueError("callback datetime must be timezone-aware")
        return self


class Escalation(_Contract):
    reason: str = Field(min_length=1)  # gateway's EscalationReason value (T-GW-01)
    target: str = Field(min_length=1)  # human extension, e.g. "200", or "callback_queue"


class Versions(_Contract):
    """What produced this call's behaviour (F11, constitution VII). Computed by T-OBS-02."""

    prompt: str = Field(min_length=1)
    policy: str = Field(min_length=1)
    model: str = Field(min_length=1)


class StageLatency(_Contract):
    p50_ms: int = Field(ge=0)
    p90_ms: int = Field(ge=0)


class CallOutcome(_Contract):
    """One row of structured data per call — PRD §8."""

    call_id: CallId
    direction: Direction
    campaign: Campaign | None = None
    customer_id: int | None = None
    loan_id: int | None = None
    contact_result: ContactResult
    verified: bool = False
    disclosures_given: list[str] = Field(default_factory=list)  # "ai_recording@1", "collections@1"
    intent: list[Intent] = Field(default_factory=list)
    reason_code: ReasonCode | None = None
    promise_to_pay: PromiseToPay | None = None
    callback: Callback | None = None
    escalated: Escalation | None = None
    dispute_flag: bool = False
    cease_contact_flag: bool = False
    sentiment: Sentiment = Sentiment.unknown
    summary: str = ""  # already redacted (T-OBS-01)
    kb_sources: list[str] = Field(default_factory=list)  # "doc_id@version#section"
    versions: Versions
    latency_p50: int | None = Field(None, ge=0)  # ms, end of speech → first agent audio
    latency_p90: int | None = Field(None, ge=0)
    latency_stages: dict[str, StageLatency] = Field(default_factory=dict)  # stt, llm_ttft, tts_ttfb, e2e
    recording_uri: str | None = None
    transcript_uri: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.verified and self.contact_result is not ContactResult.right_party:
            raise ValueError("verified=true requires contact_result=right_party")
        if self.promise_to_pay is not None and not self.verified:
            raise ValueError("promise_to_pay requires a verified customer")
        return self
