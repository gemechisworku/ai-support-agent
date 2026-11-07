"""Masking, formatting and view models for the console.

- Masking (`mask_number`, `mask_reference`) and the console API's view models + builders (T-CON-09),
  which are the only shapes the Next.js console sees.
- Row helpers for the Streamlit pages (removed with them in T-CON-14).

No I/O here, so everything is unit-testable. Pages compute no figures of their own (CON-R8).
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from supportagent.contracts import bank as b
from supportagent.contracts.kb import PolicyChunk

BANNER = "Synthetic data · local POC — every customer, loan and policy here is fake."
DASH = "—"


def mask_number(value: object) -> str:
    """Show only the last 4 digits of any account, loan, card or phone number: `•••• 1234`."""
    if value is None:
        return DASH
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return DASH
    return f"•••• {digits[-4:]}"


_LONG_DIGITS = re.compile(r"\d{5,}")


def mask_reference(value: str | None) -> str:
    """Mask every run of 5+ digits inside free text (e.g. `PMT-5520264417-001` → `PMT-••••4417-001`)."""
    if not value:
        return DASH
    return _LONG_DIGITS.sub(lambda m: f"••••{m.group()[-4:]}", value)


def money(value: Decimal | None) -> str:
    return DASH if value is None else f"${value:,.2f}"


def date(value: dt.date | dt.datetime | None) -> str:
    if value is None:
        return DASH
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    return value.isoformat()


def human_date(value: dt.date | None) -> str:
    """`4 November 2026`, as the agent would say it."""
    return DASH if value is None else f"{value.day} {value.strftime('%B %Y')}"


def flag(value: bool) -> str:
    return "yes" if value else "no"


# --------------------------------------------------------------------------- customers (CON-R8)


def customer_rows(page: b.Page[b.CustomerSummary]) -> list[dict[str, Any]]:
    return [
        {
            "Customer id": c.customer_id,
            "Name": c.full_name,
            "Phone": mask_number(c.phone),
            "Segment": c.segment,
            "Loans": c.loan_count,
            "Worst bucket": c.worst_bucket or DASH,
        }
        for c in page.items
    ]


def loan_rows(statuses: list[b.LoanStatus]) -> list[dict[str, Any]]:
    return [
        {
            "Loan": mask_number(s.number_last4),
            "Product": s.product_name,
            "Status": s.status,
            "Outstanding": money(s.outstanding_balance),
            "Overdue": money(s.overdue_amount),
            "Oldest unpaid due": date(s.oldest_unpaid_due_date),
            "Days past due": s.days_past_due,
            "Bucket": s.bucket,
            "Next due": date(s.next_due_date),
            "Next amount": money(s.next_due_amount),
            "Fees accrued": money(s.fees_accrued),
            "As of": date(s.as_of),
        }
        for s in statuses
    ]


def loan_headline(s: b.LoanStatus) -> str:
    """One line per loan, e.g. `•••• 4417 · Personal Loan · 5 DPD · $200.72 overdue since 4 November 2026`."""
    parts = [mask_number(s.number_last4), s.product_name]
    if s.days_past_due:
        parts.append(f"{s.days_past_due} DPD")
        parts.append(f"{money(s.overdue_amount)} overdue since {human_date(s.oldest_unpaid_due_date)}")
    else:
        parts.append(f"next {money(s.next_due_amount)} due {human_date(s.next_due_date)}")
    return " · ".join(parts)


def installment_rows(rows: list[b.Installment]) -> list[dict[str, Any]]:
    return [
        {
            "#": i.seq,
            "Due": date(i.due_date),
            "Principal": money(i.principal_due),
            "Interest": money(i.interest_due),
            "Fee": money(i.fee_due),
            "Paid": money(i.paid_amount),
            "Paid on": date(i.paid_date),
            "Status": i.status,
        }
        for i in rows
    ]


def transaction_rows(rows: list[b.Transaction]) -> list[dict[str, Any]]:
    return [
        {
            "When": date(t.ts),
            "Amount": money(t.amount),
            "Channel": t.channel,
            "Status": "⏳ pending" if t.status == "pending" else "posted",
            "Reference": mask_reference(t.reference),
        }
        for t in rows
    ]


def preference_rows(p: b.ContactPreferences) -> list[dict[str, Any]]:
    window = p.preferred_window or DASH  # "HH:MM-HH:MM"
    return [
        {"Preference": "Consent to AI voice calls", "Value": flag(p.consent_voice_ai)},
        {"Preference": "Do not call", "Value": flag(p.dnc)},
        {"Preference": "Cease contact requested", "Value": flag(p.cease_contact)},
        {"Preference": "Open dispute", "Value": flag(p.dispute_open)},
        {"Preference": "Preferred window", "Value": window},
        {"Preference": "Updated", "Value": date(p.updated_at)},
    ]


def interaction_rows(rows: list[b.Interaction]) -> list[dict[str, Any]]:
    out = []
    for i in rows:
        outcome = i.outcome
        out.append(
            {
                "Started": date(i.started_at),
                "Call id": i.call_id,
                "Direction": i.direction.value,
                "Campaign": i.campaign.value if i.campaign else DASH,
                "Result": i.contact_result.value,
                "Verified": flag(outcome.verified) if outcome else DASH,
                "Reason": outcome.reason_code.value if outcome and outcome.reason_code else DASH,
                "Summary": outcome.summary if outcome else DASH,
            }
        )
    return out


def promise_rows(rows: list[b.Promise]) -> list[dict[str, Any]]:
    return [
        {
            "Promise": p.id,
            "Loan": p.loan_id,
            "Date": date(p.promised_date),
            "Amount": money(p.amount),
            "Status": p.status,
            "Call id": p.call_id or DASH,
        }
        for p in rows
    ]


def task_rows(rows: list[b.Task]) -> list[dict[str, Any]]:
    return [
        {
            "Task": t.id,
            "Type": t.type,
            "Reason": t.reason,
            "Due": date(t.due_at),
            "Owner": t.owner or DASH,
            "Status": t.status,
            "Summary": t.summary,
        }
        for t in rows
    ]


# --------------------------------------------------------------------------- policies (CON-R9)


def chunk_rows(chunks: list[PolicyChunk]) -> list[dict[str, Any]]:
    return [
        {
            "Citation": c.citation,
            "Title": c.title,
            "RRF score": round(c.rrf_score, 4),
            "Similarity": DASH if c.similarity is None else round(c.similarity, 3),
        }
        for c in chunks
    ]


# --------------------------------------------------------------------------- personas and audit (CON-R10)


def persona_rows(cards: list[b.PersonaCard]) -> list[dict[str, Any]]:
    """The only table that shows a DOB: these are the personas' synthetic test identities."""
    return [
        {
            "Customer id": p.customer_id,
            "Name": p.full_name,
            "Extension": p.extension,
            "Phone": p.phone,
            "DOB (test identity)": date(p.dob),
            "Loan last 4": p.loan_last4,
            "Product": p.product_code,
            "Situation": p.situation,
            "Expected outcome": p.expected_outcome,
        }
        for p in cards
    ]


def audit_rows(rows: list[b.AuditRow]) -> list[dict[str, Any]]:
    """Audit rows in time order; denials and errors are marked so they stand out."""
    marks = {"allow": "allow", "deny": "⛔ deny", "error": "⚠️ error"}
    return [
        {
            "Time": date(r.ts),
            "Tool": r.tool,
            "Decision": marks[r.decision],
            "Latency (ms)": r.latency_ms,
            "Customer": r.customer_id if r.customer_id is not None else DASH,
        }
        for r in sorted(rows, key=lambda r: (r.ts, r.id))
    ]


# =========================================================================== console API view models
# (T-CON-09, CON-R7/R11). These are the console API's response models and, through its OpenAPI, the
# Next.js console's types. They are allow-lists: a field reaches the UI only if it is named here, and
# every identifier is masked by the builders below before it leaves Python. Typed values (Decimal,
# dates) stay typed; display formatting is the UI's job.


class _View(BaseModel):
    model_config = ConfigDict(frozen=True)


class CustomerRow(_View):
    customer_id: int
    full_name: str
    phone: str  # masked
    segment: str
    loan_count: int
    worst_bucket: str | None


class CustomerProfile(_View):
    customer_id: int
    full_name: str
    phone: str  # masked
    email: str
    address: str
    timezone: str
    segment: str
    kyc_status: str
    preferred_language: str


class ContactPreferencesView(_View):
    consent_voice_ai: bool
    dnc: bool
    cease_contact: bool
    dispute_open: bool
    preferred_window: str | None  # "HH:MM-HH:MM"
    updated_at: dt.datetime


class InstallmentView(_View):
    seq: int
    due_date: dt.date
    principal_due: Decimal
    interest_due: Decimal
    fee_due: Decimal
    paid_amount: Decimal
    paid_date: dt.date | None
    status: str


class TransactionView(_View):
    id: int
    ts: dt.datetime
    amount: Decimal
    channel: str
    status: str  # "posted" | "pending"
    reference: str  # masked


class LoanRow(_View):
    loan_id: int
    customer_id: int
    number: str  # masked, "•••• 4417"
    product_code: str
    product_name: str
    status: str
    as_of: dt.date
    outstanding_balance: Decimal
    overdue_amount: Decimal
    oldest_unpaid_due_date: dt.date | None
    next_due_date: dt.date | None
    next_due_amount: Decimal | None
    days_past_due: int
    bucket: str
    fees_accrued: Decimal
    headline: str  # e.g. "•••• 4417 · Personal Loan · 5 DPD · $200.72 overdue since 4 November 2026"


class LoanDetail(LoanRow):
    installments: list[InstallmentView]
    transactions: list[TransactionView]  # newest first, at most 20


class InteractionView(_View):
    call_id: str
    direction: str
    campaign: str | None
    started_at: dt.datetime
    ended_at: dt.datetime | None
    contact_result: str
    verified: bool | None
    reason_code: str | None
    summary: str | None


class PromiseView(_View):
    id: int
    call_id: str | None
    loan_id: int
    promised_date: dt.date
    amount: Decimal
    status: str


class TaskView(_View):
    id: int
    call_id: str | None
    type: str
    reason: str
    summary: str
    due_at: dt.datetime
    owner: str | None
    status: str


class CustomerDetail(_View):
    profile: CustomerProfile
    preferences: ContactPreferencesView
    loans: list[LoanDetail]
    interactions: list[InteractionView]
    open_promises: list[PromiseView]
    open_tasks: list[TaskView]


class PersonaView(_View):
    """The persona test sheet: the only view with a DOB and a full phone (synthetic test identities)."""

    customer_id: int
    full_name: str
    phone: str
    extension: str
    dob: dt.date
    loan_id: int
    loan_last4: str
    product_code: str
    situation: str
    expected_outcome: str


class AuditView(_View):
    id: int
    ts: dt.datetime
    tool: str
    decision: str  # "allow" | "deny" | "error"
    latency_ms: int
    customer_id: int | None


class Overview(_View):
    customers: int
    loans: int
    loans_by_bucket: dict[str, int]
    kb_documents: int
    personas: int


class Paged[V](_View):
    items: list[V]
    total: int
    limit: int
    offset: int


def customer_row(c: b.CustomerSummary) -> CustomerRow:
    return CustomerRow(
        customer_id=c.customer_id,
        full_name=c.full_name,
        phone=mask_number(c.phone),
        segment=c.segment,
        loan_count=c.loan_count,
        worst_bucket=c.worst_bucket,
    )


def customer_profile(c: b.Customer) -> CustomerProfile:
    return CustomerProfile(
        customer_id=c.id,
        full_name=c.full_name,
        phone=mask_number(c.phone),
        email=c.email,
        address=c.address,
        timezone=c.timezone,
        segment=c.segment,
        kyc_status=c.kyc_status,
        preferred_language=c.preferred_language,
    )


def preferences_view(p: b.ContactPreferences) -> ContactPreferencesView:
    return ContactPreferencesView(
        consent_voice_ai=p.consent_voice_ai,
        dnc=p.dnc,
        cease_contact=p.cease_contact,
        dispute_open=p.dispute_open,
        preferred_window=str(p.preferred_window) if p.preferred_window else None,
        updated_at=p.updated_at,
    )


def installment_view(i: b.Installment) -> InstallmentView:
    return InstallmentView(
        seq=i.seq,
        due_date=i.due_date,
        principal_due=i.principal_due,
        interest_due=i.interest_due,
        fee_due=i.fee_due,
        paid_amount=i.paid_amount,
        paid_date=i.paid_date,
        status=i.status,
    )


def transaction_view(t: b.Transaction) -> TransactionView:
    return TransactionView(
        id=t.id,
        ts=t.ts,
        amount=t.amount,
        channel=t.channel,
        status=t.status,
        reference=mask_reference(t.reference),
    )


def loan_row(s: b.LoanStatus) -> LoanRow:
    return LoanRow(**_loan_fields(s))


def loan_detail(
    s: b.LoanStatus, installments: list[b.Installment], transactions: list[b.Transaction]
) -> LoanDetail:
    return LoanDetail(
        **_loan_fields(s),
        installments=[installment_view(i) for i in sorted(installments, key=lambda i: i.seq)],
        transactions=[transaction_view(t) for t in transactions],
    )


def _loan_fields(s: b.LoanStatus) -> dict[str, Any]:
    return {
        "loan_id": s.loan_id,
        "customer_id": s.customer_id,
        "number": mask_number(s.number_last4),
        "product_code": s.product_code,
        "product_name": s.product_name,
        "status": s.status,
        "as_of": s.as_of,
        "outstanding_balance": s.outstanding_balance,
        "overdue_amount": s.overdue_amount,
        "oldest_unpaid_due_date": s.oldest_unpaid_due_date,
        "next_due_date": s.next_due_date,
        "next_due_amount": s.next_due_amount,
        "days_past_due": s.days_past_due,
        "bucket": s.bucket,
        "fees_accrued": s.fees_accrued,
        "headline": loan_headline(s),
    }


def interaction_view(i: b.Interaction) -> InteractionView:
    o = i.outcome
    return InteractionView(
        call_id=i.call_id,
        direction=i.direction.value,
        campaign=i.campaign.value if i.campaign else None,
        started_at=i.started_at,
        ended_at=i.ended_at,
        contact_result=i.contact_result.value,
        verified=o.verified if o else None,
        reason_code=o.reason_code.value if o and o.reason_code else None,
        summary=o.summary if o else None,
    )


def promise_view(p: b.Promise) -> PromiseView:
    return PromiseView(
        id=p.id,
        call_id=p.call_id,
        loan_id=p.loan_id,
        promised_date=p.promised_date,
        amount=p.amount,
        status=p.status,
    )


def task_view(t: b.Task) -> TaskView:
    return TaskView(
        id=t.id,
        call_id=t.call_id,
        type=t.type,
        reason=t.reason,
        summary=t.summary,
        due_at=t.due_at,
        owner=t.owner,
        status=t.status,
    )


def persona_view(p: b.PersonaCard) -> PersonaView:
    return PersonaView(
        customer_id=p.customer_id,
        full_name=p.full_name,
        phone=p.phone,
        extension=p.extension,
        dob=p.dob,
        loan_id=p.loan_id,
        loan_last4=p.loan_last4,
        product_code=p.product_code,
        situation=p.situation,
        expected_outcome=p.expected_outcome,
    )


def audit_view(r: b.AuditRow) -> AuditView:
    return AuditView(
        id=r.id,
        ts=r.ts,
        tool=r.tool,
        decision=r.decision,
        latency_ms=r.latency_ms,
        customer_id=r.customer_id,
    )
