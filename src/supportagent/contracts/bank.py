"""Contracts of the mock core bank's internal API (specs/02-mock-bank, owned by T-BANK-01).

The bank API is trusted-internal: these models carry **unmasked** data. Only the gateway and the
scheduler consume them; masking for the agent is the gateway's job (specs/04-gateway).

Money is `Money` (Decimal, 2 dp, serialised as a string); dates are `date`; instants are timezone-aware
(`AwareDatetime` rejects naive values).
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from supportagent.contracts.money import Money
from supportagent.contracts.outcome import CallId, CallOutcome, Campaign, ContactResult, Direction, Versions

Phone = Annotated[str, Field(pattern=r"^\+[1-9]\d{6,14}$", examples=["+15550100302"])]
"""E.164 phone number. All seeded numbers are in the reserved +1555 range."""

Bucket = Literal["current", "1-30", "31-60", "61-90", "90+"]
ProductCode = Literal["PERSONAL", "AUTO", "MORTGAGE", "SME_WC", "SALARY_ADV"]
TimeWindow = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")]
"""Local time window "HH:MM-HH:MM", e.g. "18:00-20:00"."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


# --------------------------------------------------------------------------- reference data


class LateFeeRule(_Contract):
    """`flat`: a fixed amount; `pct`: a percentage of the overdue principal + interest, optionally capped."""

    type: Literal["flat", "pct"]
    value: Decimal = Field(gt=0)
    cap: Money | None = None


class LoanProduct(_Contract):
    code: ProductCode
    name: str
    rate_type: Literal["fixed", "variable"]
    apr_min: Decimal = Field(ge=0, decimal_places=2)
    apr_max: Decimal = Field(ge=0, decimal_places=2)
    term_min: int = Field(ge=1)
    term_max: int = Field(ge=1)
    grace_days: int = Field(ge=0)
    late_fee_rule: LateFeeRule
    policy_doc_id: str


# --------------------------------------------------------------------------- customers and ledger


class Customer(_Contract):
    id: int
    full_name: str
    dob: dt.date
    phone: Phone
    email: str
    address: str
    timezone: str  # IANA, e.g. "America/New_York"
    segment: str
    kyc_status: str
    preferred_language: str = "en"


class Account(_Contract):
    id: int
    customer_id: int
    type: Literal["current", "savings"]
    number: str
    balance: Money
    status: Literal["active", "closed"]


class Loan(_Contract):
    id: int
    customer_id: int
    product_code: ProductCode
    number: str
    principal: Money
    apr: Decimal = Field(ge=0, decimal_places=2)
    term_months: int = Field(ge=1)
    start_date: dt.date
    status: Literal["active", "closed", "default"]
    days_past_due: int = Field(ge=0)  # cached by the seed; LoanStatus recomputes for as_of
    bucket: Bucket
    repayment_account_id: int


class LoanStatus(_Contract):
    """Derived figures for one loan at `as_of`, computed by `bank/ledger.py` from installments (BANK-R5)."""

    loan_id: int
    customer_id: int
    number_last4: str = Field(min_length=4, max_length=4)
    product_code: ProductCode
    product_name: str
    as_of: dt.date
    outstanding_balance: Money  # Σ unpaid principal + interest + fees over the whole schedule
    overdue_amount: Money  # Σ unpaid amounts of instalments due before as_of
    oldest_unpaid_due_date: dt.date | None
    next_due_date: dt.date | None  # first unpaid instalment due on/after as_of
    next_due_amount: Money | None
    days_past_due: int = Field(ge=0)
    bucket: Bucket
    fees_accrued: Money  # unpaid late fees
    status: Literal["active", "closed", "default"]


class Installment(_Contract):
    loan_id: int
    seq: int = Field(ge=1)
    due_date: dt.date
    principal_due: Money
    interest_due: Money
    fee_due: Money
    paid_amount: Money
    paid_date: dt.date | None
    status: Literal["scheduled", "paid", "partial", "missed"]


class Transaction(_Contract):
    id: int
    account_id: int
    loan_id: int | None
    ts: AwareDatetime
    amount: Money
    channel: Literal["branch", "mobile", "transfer"]
    status: Literal["posted", "pending"]
    reference: str


# --------------------------------------------------------------------------- contact preferences


class ContactPreferences(_Contract):
    customer_id: int
    consent_voice_ai: bool
    dnc: bool
    preferred_window: TimeWindow | None
    cease_contact: bool
    dispute_open: bool
    updated_at: AwareDatetime


class ContactPreferencesPatch(_Contract):
    """Only the fields that are set are changed (PATCH semantics)."""

    consent_voice_ai: bool | None = None
    dnc: bool | None = None
    preferred_window: TimeWindow | None = None
    cease_contact: bool | None = None
    dispute_open: bool | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("set at least one field")
        return self


class EligibleLoan(_Contract):
    """Facts the compliance gate needs about one candidate loan; the bank does not apply contact rules."""

    loan_id: int
    customer_id: int
    product_code: ProductCode
    timezone: str
    phone: Phone
    days_past_due: int = Field(ge=0)
    bucket: Bucket
    next_due_date: dt.date | None
    amount_due: Money  # overdue amount if past due, else the next instalment's amount
    prefs: ContactPreferences
    attempts_last_7d: int = Field(ge=0)
    last_conversation_at: AwareDatetime | None


# --------------------------------------------------------------------------- interaction tables


class InteractionIn(_Contract):
    """A call record. Upserted by `call_id`: the scheduler writes the attempt, the worker completes it."""

    call_id: CallId
    customer_id: int | None = None
    loan_id: int | None = None
    direction: Direction
    campaign: Campaign | None = None
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    contact_result: ContactResult
    outcome: CallOutcome | None = None  # the full PRD §8 record, once the call has ended
    transcript_uri: str | None = None
    recording_uri: str | None = None

    @model_validator(mode="after")
    def _outcome_matches(self) -> Self:
        if self.outcome is not None and self.outcome.call_id != self.call_id:
            raise ValueError("outcome.call_id must equal call_id")
        return self


class Interaction(_Contract):
    call_id: CallId
    customer_id: int | None
    loan_id: int | None
    direction: Direction
    campaign: Campaign | None
    started_at: AwareDatetime
    ended_at: AwareDatetime | None
    contact_result: ContactResult
    outcome: CallOutcome | None
    transcript_uri: str | None
    recording_uri: str | None
    versions: Versions | None


class PromiseIn(_Contract):
    """Business validation (window, amount) is the gateway's job; the bank checks types and the loan."""

    call_id: str | None = None
    loan_id: int
    promised_date: dt.date
    amount: Money = Field(gt=0)


class Promise(_Contract):
    id: int
    call_id: str | None
    loan_id: int
    promised_date: dt.date
    amount: Money
    status: Literal["open", "kept", "broken"]
    created_at: AwareDatetime


class TaskIn(_Contract):
    call_id: str | None = None
    customer_id: int
    loan_id: int | None = None
    type: Literal["callback", "escalation"]
    reason: str = Field(min_length=1)
    summary: str = ""
    due_at: AwareDatetime
    owner: str | None = None


class Task(_Contract):
    id: int
    call_id: str | None
    customer_id: int
    loan_id: int | None
    type: Literal["callback", "escalation"]
    reason: str
    summary: str
    due_at: AwareDatetime
    owner: str | None
    status: Literal["open", "done", "cancelled"]
    created_at: AwareDatetime


class InteractionHistory(_Contract):
    """`GET /customers/{id}/interactions`: recent calls plus open promises and tasks, for continuity."""

    interactions: list[Interaction]
    open_promises: list[Promise]
    open_tasks: list[Task]


class AuditIn(_Contract):
    """One gateway decision (append-only). `call_id` is "service" for service-scope calls."""

    ts: AwareDatetime
    call_id: str
    tool: str
    args_hash: str = Field(pattern=r"^[0-9a-f]{64}$")  # sha256 hex
    customer_id: int | None = None
    decision: Literal["allow", "deny", "error"]
    latency_ms: int = Field(ge=0)


class OutboxMessage(_Contract):
    """A post-call summary waiting to be sent (F13, mocked: nothing is actually sent)."""

    id: int
    call_id: str
    channel: Literal["sms", "email"]
    recipient: str
    body: str
    status: Literal["pending", "sent"]
    created_at: AwareDatetime


class Page[T](BaseModel):
    """A page of results for the operations endpoints (T-BANK-07)."""

    model_config = ConfigDict(frozen=True)

    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


# --------------------------------------------------------------------------- operations read API (T-BANK-07)


class AuditRow(_Contract):
    """One stored audit-log row (the `AuditIn` written by the gateway, plus its id)."""

    id: int
    ts: AwareDatetime
    call_id: str
    tool: str
    args_hash: str
    customer_id: int | None
    decision: Literal["allow", "deny", "error"]
    latency_ms: int


class CallDetail(_Contract):
    """`GET /interactions/{call_id}`: everything recorded for one call, for the console's call view."""

    interaction: Interaction
    customer_timezone: str | None  # for calling-window checks in the customer's local time
    promises: list[Promise]
    tasks: list[Task]
    audit: list[AuditRow]  # time order
    outbox: list[OutboxMessage]


class CustomerSummary(_Contract):
    """One row of the console's customer search (T-BANK-09)."""

    customer_id: int
    full_name: str
    phone: Phone
    segment: str
    loan_count: int = Field(ge=0)
    worst_bucket: Bucket | None  # the most delinquent loan's bucket at the bank's as_of; None = no loans


class PersonaCard(_Contract):
    """A scripted persona for role-play testing (T-BANK-09).

    These are **synthetic test identities**: the name, DOB and loan last 4 a tester gives when playing
    the persona on a softphone. They are fake and exist only in the mock bank's seed.
    """

    customer_id: int
    full_name: str
    phone: Phone
    extension: str
    dob: dt.date
    loan_id: int
    loan_last4: str = Field(min_length=4, max_length=4)
    product_code: ProductCode
    situation: str
    expected_outcome: str


class TaskPatch(_Contract):
    """`PATCH /tasks/{id}`: the console's follow-up queue marks tasks done or assigns them."""

    status: Literal["open", "done", "cancelled"] | None = None
    owner: str | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("set at least one field")
        return self
