"""Compliance contracts (specs/05-compliance): contact rules, gate facts and decisions.

`ContactRules` mirrors `data/config/contact_rules.yaml` (COMP-R1) and is loaded by
`supportagent.compliance.config.load_rules`. `ContactFacts` and `GateDecision` are the input and output
of the contact gate (T-COMP-02..04), which the scheduler calls before every dial.
"""

import datetime as dt
import re
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _clock_time(value: Any) -> Any:
    """Accept only "HH:MM" strings (or `time`): YAML reads unquoted 08:00 as the number 480."""
    if isinstance(value, dt.time):
        return value
    if isinstance(value, str) and _HHMM.match(value):
        return value
    raise ValueError('must be a quoted "HH:MM" time, e.g. "08:00"')


ClockTime = Annotated[dt.time, BeforeValidator(_clock_time)]
TimeWindow = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- rules (config)


class CallingWindow(_Contract):
    """Local-time window for outbound calls: `start` inclusive, `end` exclusive."""

    start: ClockTime
    end: ClockTime

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError(f"end ({self.end:%H:%M}) must be after start ({self.start:%H:%M})")
        return self


class FrequencyCap(_Contract):
    max_attempts: int = Field(ge=1)
    window_days: int = Field(ge=1)
    cool_off_days_after_conversation: int = Field(ge=0)


class RetryRules(_Contract):
    no_answer_retry_minutes: int = Field(ge=1)
    busy_retry_minutes: int = Field(ge=1)
    voicemail_retry_minutes: int = Field(ge=1)
    max_attempts_per_day: int = Field(ge=1)


class PreDueCampaign(_Contract):
    due_within_days: int = Field(ge=0)


class MissedPaymentCampaign(_Contract):
    dpd_min: int = Field(ge=1)
    dpd_max: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.dpd_max < self.dpd_min:
            raise ValueError(f"dpd_max ({self.dpd_max}) must be >= dpd_min ({self.dpd_min})")
        return self


class CampaignRules(_Contract):
    pre_due_reminder: PreDueCampaign
    missed_payment: MissedPaymentCampaign


class ContactRules(_Contract):
    """The versioned contact rules (COMP-R1). `version_tag` is recorded with every decision (COMP-R7)."""

    version: str = Field(pattern=r"^\d+\.\d+$")
    jurisdiction: str = Field(min_length=1)
    calling_window: CallingWindow
    frequency: FrequencyCap
    retries: RetryRules
    campaigns: CampaignRules

    @property
    def version_tag(self) -> str:
        return f"contact_rules@{self.version}"


# --------------------------------------------------------------------------- gate (T-COMP-02..04)


class DenyReason(StrEnum):
    dnc = "dnc"
    no_consent = "no_consent"
    cease_contact = "cease_contact"
    dispute_open = "dispute_open"
    outside_window = "outside_window"
    outside_preferred_window = "outside_preferred_window"
    frequency_cap = "frequency_cap"
    cool_off = "cool_off"
    unknown_timezone = "unknown_timezone"


class ContactFacts(_Contract):
    """What the gate knows about one loan and its customer at `planned_at` (supplied by the bank)."""

    loan_id: int
    customer_id: int
    timezone: str | None  # IANA name; None fails closed (unknown_timezone)
    preferred_window: TimeWindow | None = None
    dnc: bool
    consent_voice_ai: bool
    cease_contact: bool
    dispute_open: bool
    attempts_last_7d: int = Field(ge=0)
    last_conversation_at: AwareDatetime | None = None
    planned_at: AwareDatetime


class GateDecision(_Contract):
    """Allowed only when `reasons` is empty; every applicable reason is listed (COMP-R4)."""

    allowed: bool
    reasons: list[DenyReason] = Field(default_factory=list)
    rules_version: str  # e.g. "contact_rules@1.0" (COMP-R7)
    evaluated_at: AwareDatetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.allowed == bool(self.reasons):
            raise ValueError("allowed must be true exactly when there are no deny reasons")
        return self


# --------------------------------------------------------------------------- disclosures (T-COMP-05)


class Disclosure(_Contract):
    """A disclosure script as configured: `text` may contain `{placeholders}`."""

    key: str
    version: str
    text: str


class RenderedDisclosure(_Contract):
    """The exact words to speak. `tag` (`key@version`) goes into `CallOutcome.disclosures_given`."""

    key: str
    version: str
    text: str

    @property
    def tag(self) -> str:
        return f"{self.key}@{self.version}"


class CallEvent(_Contract):
    """One step of a call, from the agent/gateway audit trail, for after-the-fact checks.

    `kind="tool"`: a gateway tool call (`name` = tool); `kind="verified"`: identity bound;
    `kind="disclosure"`: a disclosure was spoken (`name` = key); `kind="utterance"`: anything said.
    """

    ts: AwareDatetime
    kind: Literal["tool", "disclosure", "verified", "utterance"]
    name: str = ""


class Violation(_Contract):
    """A failed disclosure rule.

    `code` is `missing_disclosure:<key>`, `data_before_verification` or `data_before_collections_disclosure`.
    """

    code: str
    detail: str
