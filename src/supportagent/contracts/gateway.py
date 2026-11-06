"""Contracts of the agent data gateway (specs/04-gateway, owned by T-GW-01).

Three surfaces:
- the **session API** (worker credential): `SessionCreate` → `SessionCreated`;
- the **MCP tools** (session token): one `*Input` and one `*Output` per tool, always wrapped in a
  `ToolResult` envelope (`ok`, `data`, `error`);
- the **service API** (scheduler credential, T-GW-10): `Candidate`, `ContactPrefs`, `CallAttempt`.

Guardrails encoded in the types (constitution II):
- every model is `extra="forbid"`, so an unexpected argument such as `customer_id` fails validation;
- no input model has a customer id; identity is bound inside the gateway by `verify_identity`;
- outputs carry masked numbers (`****1234`) and never surname, full DOB, address, email or national id.
"""

import datetime as dt
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from supportagent.contracts.bank import Phone, ProductCode, TimeWindow
from supportagent.contracts.money import Money
from supportagent.contracts.outcome import CallId, Campaign, ContactResult, Direction

Last4 = Annotated[str, Field(pattern=r"^\d{4}$", examples=["4417"])]
"""The last 4 digits of a loan number, as the caller says them."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- envelope

ToolErrorCode = Literal[
    "not_verified",
    "locked",
    "rate_limited",
    "not_found",
    "invalid_input",
    "session_closed",
    "forbidden",
    "audit_unavailable",
    "upstream_error",
]


class ToolError(_Contract):
    code: ToolErrorCode
    message: str = ""


class ToolResult[T](BaseModel):
    """Every tool returns this shape: data on success, an error code otherwise (never both)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    data: T | None = None
    error: ToolError | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.ok and self.error is not None:
            raise ValueError("ok=true must not carry an error")
        if not self.ok and (self.error is None or self.data is not None):
            raise ValueError("ok=false needs an error and no data")
        return self

    @classmethod
    def success(cls, data: T) -> "ToolResult[T]":
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, code: ToolErrorCode, message: str = "") -> "ToolResult[T]":
        return cls(ok=False, error=ToolError(code=code, message=message))


# --------------------------------------------------------------------------- session API


class SessionCreate(_Contract):
    """Sent by the agent worker's code (never the LLM) when a call starts."""

    call_id: CallId
    direction: Direction
    campaign: Campaign | None = None
    loan_id: int | None = None  # outbound: the loan the call is about → expected customer
    caller_phone: Phone | None = None  # inbound: only a hint for candidate customers


class SessionCreated(_Contract):
    session_token: str
    expires_at: AwareDatetime


# --------------------------------------------------------------------------- identity tools


class LookupByPhoneInput(_Contract):
    """No arguments: the gateway uses the phone number of the session."""


class LookupByPhoneOutput(_Contract):
    match: bool
    first_name: str | None = None  # only on outbound sessions


class VerifyIdentityInput(_Contract):
    full_name: str = Field(min_length=1, max_length=100)
    dob: dt.date
    loan_last4: Last4


class VerifyIdentityOutput(_Contract):
    verified: bool
    attempts_remaining: int = Field(ge=0)
    locked: bool
    first_name: str | None = None


# --------------------------------------------------------------------------- policy


class SearchPolicyInput(_Contract):
    query: str = Field(min_length=1, max_length=300)
    product: ProductCode | None = None


class PolicySource(_Contract):
    doc_id: str
    version: str
    section: str
    text: str  # sanitised and truncated; data, never instructions
    score: float

    @property
    def citation(self) -> str:
        return f"{self.doc_id}@{self.version}#{self.section}"


class SearchPolicyOutput(_Contract):
    chunks: list[PolicySource] = Field(max_length=4)
    low_confidence: bool


# --------------------------------------------------------------------------- verified reads


class GetLoanStatusInput(_Contract):
    loan_last4: Last4 | None = None


class LoanSummary(_Contract):
    loan_masked: str  # "****4417"
    product_code: ProductCode
    product_name: str
    outstanding_balance: Money
    overdue_amount: Money
    oldest_unpaid_due_date: dt.date | None
    next_due_date: dt.date | None
    next_due_amount: Money | None
    days_past_due: int = Field(ge=0)
    fees_accrued: Money
    status: Literal["active", "closed", "default"]
    speakable: (
        str  # e.g. "$245.10 overdue since 4 November 2026; next payment $245.10 due on 4 December 2026"
    )


class LoanStatusOutput(_Contract):
    loans: list[LoanSummary]


class GetTransactionsInput(_Contract):
    loan_last4: Last4 | None = None


class TransactionOut(_Contract):
    date: dt.date
    amount: Money
    channel: Literal["branch", "mobile", "transfer"]
    status: Literal["posted", "pending"]
    reference_masked: str
    speakable: str


class TransactionsOutput(_Contract):
    transactions: list[TransactionOut] = Field(max_length=5)


class InteractionHistoryInput(_Contract):
    pass


class CallSummary(_Contract):
    date: dt.date
    direction: Direction
    campaign: Campaign | None
    contact_result: ContactResult


class PromiseOut(_Contract):
    promised_date: dt.date
    amount: Money
    status: Literal["open", "kept", "broken"]


class TaskOut(_Contract):
    type: Literal["callback", "escalation"]
    due_at: AwareDatetime
    status: Literal["open", "done", "cancelled"]


class InteractionHistoryOutput(_Contract):
    recent_calls: list[CallSummary]
    open_promises: list[PromiseOut]
    open_tasks: list[TaskOut]


# --------------------------------------------------------------------------- writes

PromiseReason = Literal["ok", "date_out_of_window", "amount_invalid", "loan_not_found"]


class RecordPromiseInput(_Contract):
    promise_date: dt.date
    amount: Money
    loan_last4: Last4 | None = None


class RecordPromiseOutput(_Contract):
    accepted: bool
    reason: PromiseReason
    promise_id: int | None = None
    max_date: dt.date
    amount_due: Money


class RequestCallbackInput(_Contract):
    preferred_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=200)


class TaskCreatedOutput(_Contract):
    task_id: int
    type: Literal["callback", "escalation"]
    due_at: AwareDatetime


class EscalationReason(StrEnum):
    dispute = "dispute"
    hardship = "hardship"
    complaint = "complaint"
    legal = "legal"
    human_requested = "human_requested"
    verification_failed = "verification_failed"
    misunderstanding = "misunderstanding"
    other = "other"


class EscalateInput(_Contract):
    reason: EscalationReason
    summary: str = Field(min_length=1, max_length=600)


class EscalateOutput(_Contract):
    task_id: int
    human_available: bool
    transfer_extension: str | None = None


ContactFlag = Literal["dispute", "cease_contact", "preferred_window"]


class SetContactFlagInput(_Contract):
    flag: ContactFlag
    preferred_window: TimeWindow | None = None
    note: str = Field("", max_length=200)

    @model_validator(mode="after")
    def _window_iff_flag(self) -> Self:
        if (self.flag == "preferred_window") != (self.preferred_window is not None):
            raise ValueError("preferred_window is required exactly when flag='preferred_window'")
        return self


class ContactFlagOutput(_Contract):
    flag: ContactFlag
    applied: bool


class OutcomeRecordedOutput(_Contract):
    interaction_id: str  # the call_id the interaction row is keyed by


# --------------------------------------------------------------------------- service API (T-GW-10)


class ContactPrefs(_Contract):
    consent_voice_ai: bool
    dnc: bool
    cease_contact: bool
    dispute_open: bool
    preferred_window: TimeWindow | None = None


class Candidate(_Contract):
    customer_id: int
    loan_id: int
    phone: Phone
    timezone: str
    campaign: Campaign
    due_date: dt.date | None = None
    amount_due: Money | None = None
    days_past_due: int = Field(ge=0)
    contact: ContactPrefs
    attempts_last_7d: int = Field(ge=0)
    last_conversation_at: AwareDatetime | None = None
    task_id: int | None = None


class CallAttempt(_Contract):
    call_id: CallId
    customer_id: int
    loan_id: int
    campaign: Campaign
    contact_result: ContactResult
    started_at: AwareDatetime


# Tool name → (input model, output model). The MCP server (T-GW-09) registers exactly these.
TOOL_MODELS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "lookup_by_phone": (LookupByPhoneInput, LookupByPhoneOutput),
    "verify_identity": (VerifyIdentityInput, VerifyIdentityOutput),
    "search_policy": (SearchPolicyInput, SearchPolicyOutput),
    "get_loan_status": (GetLoanStatusInput, LoanStatusOutput),
    "get_recent_transactions": (GetTransactionsInput, TransactionsOutput),
    "get_interaction_history": (InteractionHistoryInput, InteractionHistoryOutput),
    "record_promise_to_pay": (RecordPromiseInput, RecordPromiseOutput),
    "request_callback": (RequestCallbackInput, TaskCreatedOutput),
    "escalate": (EscalateInput, EscalateOutput),
    "set_contact_flag": (SetContactFlagInput, ContactFlagOutput),
    # record_call_outcome takes contracts.outcome.CallOutcome (worker scope; never an LLM tool)
}
