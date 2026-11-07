"""Pure formatting helpers for the console pages: masking, money, dates and table rows.

No Streamlit and no I/O here, so everything is unit-testable. Pages only call the API clients and
render what these helpers return; they compute no figures of their own (CON-R8).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

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
            "Reference": t.reference,
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
