"""`OutcomeBuilder`: accumulates the call's PRD §8 outcome while the states run (AGENT-R11).

States record what they learned through tool results (`record_promise` with the gateway's accepted date and
amount, `record_callback`, `record_escalation`, ...); `build()` joins that with what the call state knows
(verified, who answered, disclosures spoken) and the versions, and returns a validated `CallOutcome`. It
also keeps the turns heard so far, which `finalize` hands to the redacted transcript store. The builder is
pure: no I/O, no model.

The contract's own rules still hold. Where the builder can repair an inconsistency without inventing data it
does (a verified call is a right-party call; a promise is dropped when nobody was verified), so that a bug in
a state costs a field, never the outcome row (AGENT-R11: every call ends with exactly one outcome).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from supportagent.agent.summary import compose_summary
from supportagent.common.clock import now
from supportagent.contracts.call import CallMetadata
from supportagent.contracts.outcome import (
    Callback,
    CallOutcome,
    ContactResult,
    Escalation,
    Intent,
    PromiseToPay,
    ReasonCode,
    Sentiment,
    StageLatency,
    Versions,
)
from supportagent.contracts.transcript import RawTurn, Speaker, ToolRef

# The intent an escalation reason implies (none for verification_failed, misunderstanding, other).
_ESCALATION_INTENT: dict[str, Intent] = {
    "dispute": Intent.dispute,
    "hardship": Intent.hardship,
    "complaint": Intent.complaint,
    "legal": Intent.legal,
    "human_requested": Intent.human_request,
}


@dataclass(frozen=True)
class LatencyFigures:
    """End-to-end and per-stage latency in milliseconds (T-VOICE-04's `LatencySummary.to_outcome()`)."""

    p50_ms: int | None = None
    p90_ms: int | None = None
    stages: dict[str, StageLatency] = field(default_factory=dict)


class OutcomeBuilder:
    def __init__(self, metadata: CallMetadata) -> None:
        self.metadata = metadata
        self.intents: list[Intent] = []
        self.reason_code: ReasonCode | None = None
        self.promise: PromiseToPay | None = None
        self.callback: Callback | None = None
        self.escalated: Escalation | None = None
        self.dispute_flag = False
        self.cease_contact_flag = False
        self.kb_sources: list[str] = []
        self.sentiment = Sentiment.unknown
        self.latency: LatencyFigures | None = None
        self.known_pii: list[str] = []  # values the transcript store must always redact (never persisted)
        self.turns: list[RawTurn] = []
        self.finalized = False  # set by `finalize`: the outcome is written at most once per call

    @property
    def call_id(self) -> str:
        return self.metadata.call_id

    # ------------------------------------------------------------------ what states record

    def add_intent(self, intent: Intent) -> None:
        if intent not in self.intents:
            self.intents.append(intent)

    def set_reason_code(self, code: ReasonCode) -> None:
        self.reason_code = code

    def record_promise(self, date: dt.date, amount: Decimal) -> None:
        """Call with the gateway's accepted promise (its date and amount), never with the caller's words."""
        self.promise = PromiseToPay(date=date, amount=amount)
        self.add_intent(Intent.promise_to_pay)

    def record_callback(self, when: dt.datetime) -> None:
        self.callback = Callback(datetime=when)  # raises if `when` is not timezone-aware
        self.add_intent(Intent.callback_request)

    def record_escalation(self, reason: str, target: str) -> None:
        """`target` is the human extension, or `callback_queue` when a ticket was made instead."""
        self.escalated = Escalation(reason=reason, target=target)
        intent = _ESCALATION_INTENT.get(reason)
        if intent is not None:
            self.add_intent(intent)

    def flag_dispute(self) -> None:
        self.dispute_flag = True
        self.add_intent(Intent.dispute)

    def flag_cease_contact(self) -> None:
        self.cease_contact_flag = True
        self.add_intent(Intent.cease_contact)

    def add_kb_source(self, citation: str) -> None:
        """A `doc_id@version#section` the agent answered from."""
        if citation not in self.kb_sources:
            self.kb_sources.append(citation)

    def set_latency(self, figures: LatencyFigures) -> None:
        self.latency = figures

    # ------------------------------------------------------------------ transcript material

    def know(self, *values: str) -> None:
        """Values (names, dates of birth, ...) that the transcript must redact whatever the detector finds."""
        for value in values:
            if value and value not in self.known_pii:
                self.known_pii.append(value)

    def add_turn(
        self,
        speaker: Speaker,
        text: str,
        *,
        state: str | None = None,
        tool: ToolRef | None = None,
        ts: dt.datetime | None = None,
    ) -> RawTurn:
        turn = RawTurn(speaker=speaker, text=text, ts=ts or now(), state=state, tool=tool)
        self.turns.append(turn)
        return turn

    # ------------------------------------------------------------------ build

    def build(
        self,
        *,
        verified: bool,
        contact_result: ContactResult,
        disclosures_given: Sequence[str],
        versions: Versions,
        summary: str | None = None,
        bank_name: str = "the bank",
        transcript_uri: str | None = None,
    ) -> CallOutcome:
        """The validated outcome. `summary=None` composes the template from the fields recorded here."""
        if verified:
            contact_result = ContactResult.right_party
        promise = self.promise if verified else None
        intents = [i for i in self.intents if verified or i is not Intent.promise_to_pay]
        text = summary
        if text is None:
            text = compose_summary(
                bank_name=bank_name,
                verified=verified,
                contact_result=contact_result,
                intents=intents,
                promise=promise,
                callback=self.callback,
                escalated=self.escalated,
                dispute_flag=self.dispute_flag,
                cease_contact_flag=self.cease_contact_flag,
            )
        latency = self.latency
        return CallOutcome(
            call_id=self.metadata.call_id,
            direction=self.metadata.direction,
            campaign=self.metadata.campaign,
            customer_id=self.metadata.customer_id,
            loan_id=self.metadata.loan_id,
            contact_result=contact_result,
            verified=verified,
            disclosures_given=list(disclosures_given),
            intent=intents,
            reason_code=self.reason_code,
            promise_to_pay=promise,
            callback=self.callback,
            escalated=self.escalated,
            dispute_flag=self.dispute_flag,
            cease_contact_flag=self.cease_contact_flag,
            sentiment=self.sentiment,
            summary=text,
            kb_sources=list(self.kb_sources),
            versions=versions,
            latency_p50=latency.p50_ms if latency else None,
            latency_p90=latency.p90_ms if latency else None,
            latency_stages=dict(latency.stages) if latency else {},
            transcript_uri=transcript_uri,
        )
