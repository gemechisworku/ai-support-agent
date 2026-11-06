"""Derived loan figures — the single source of truth for balances, DPD, buckets and fees (BANK-R5).

Pure functions over instalment rows; no I/O. The seed uses them to build schedules and refresh the
cached `loans.days_past_due`/`bucket`; the API recomputes `LoanStatus` with them for `as_of`.

Definitions (match docs/prd.md and data/policies/delinquency-stages.md):
- An instalment is **unpaid** while `principal_due + interest_due + fee_due - paid_amount > 0`.
- It is **past due** when it is unpaid and `due_date < as_of`.
- **Days past due** = days since the oldest past-due instalment's due date (the day after the due
  date is 1 DPD). The grace period does not change DPD; it only decides when the late fee is added.
- The **late fee** is added once per instalment still unpaid when the product's grace period ends,
  i.e. when `as_of > due_date + grace_days`.
"""

import calendar
import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Protocol

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
Bucket = Literal["current", "1-30", "31-60", "61-90", "90+"]


class InstallmentLike(Protocol):
    @property
    def due_date(self) -> dt.date: ...
    @property
    def principal_due(self) -> Decimal: ...
    @property
    def interest_due(self) -> Decimal: ...
    @property
    def fee_due(self) -> Decimal: ...
    @property
    def paid_amount(self) -> Decimal: ...


@dataclass(frozen=True)
class Line:
    """A minimal instalment (tests and the seed)."""

    due_date: dt.date
    principal_due: Decimal
    interest_due: Decimal
    fee_due: Decimal = ZERO
    paid_amount: Decimal = ZERO


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def total_due(inst: InstallmentLike) -> Decimal:
    return money(inst.principal_due + inst.interest_due + inst.fee_due)


def remaining(inst: InstallmentLike) -> Decimal:
    return max(ZERO, money(total_due(inst) - inst.paid_amount))


def is_unpaid(inst: InstallmentLike) -> bool:
    return remaining(inst) > ZERO


def past_due(insts: Iterable[InstallmentLike], as_of: dt.date) -> list[InstallmentLike]:
    return sorted((i for i in insts if is_unpaid(i) and i.due_date < as_of), key=lambda i: i.due_date)


def outstanding(insts: Iterable[InstallmentLike]) -> Decimal:
    """Σ unpaid principal + interest + fees over the whole schedule."""
    return money(sum((remaining(i) for i in insts), ZERO))


def overdue_amount(insts: Iterable[InstallmentLike], as_of: dt.date) -> Decimal:
    return money(sum((remaining(i) for i in past_due(insts, as_of)), ZERO))


def oldest_unpaid_due_date(insts: Iterable[InstallmentLike], as_of: dt.date) -> dt.date | None:
    late = past_due(insts, as_of)
    return late[0].due_date if late else None


def days_past_due(insts: Iterable[InstallmentLike], as_of: dt.date) -> int:
    oldest = oldest_unpaid_due_date(insts, as_of)
    return (as_of - oldest).days if oldest else 0


def bucket(dpd: int) -> Bucket:
    if dpd <= 0:
        return "current"
    if dpd <= 30:
        return "1-30"
    if dpd <= 60:
        return "31-60"
    if dpd <= 90:
        return "61-90"
    return "90+"


def next_due(insts: Iterable[InstallmentLike], as_of: dt.date) -> tuple[dt.date, Decimal] | None:
    """The first unpaid instalment due on or after `as_of`, with its remaining amount."""
    upcoming = sorted((i for i in insts if is_unpaid(i) and i.due_date >= as_of), key=lambda i: i.due_date)
    return (upcoming[0].due_date, remaining(upcoming[0])) if upcoming else None


def fees_accrued(insts: Iterable[InstallmentLike]) -> Decimal:
    """Unpaid late fees (payments are applied to principal and interest first)."""
    total = ZERO
    for i in insts:
        unpaid_fee = min(i.fee_due, max(ZERO, total_due(i) - i.paid_amount))
        total += unpaid_fee
    return money(total)


# --------------------------------------------------------------------------- fees and schedules


def fee_applies(due_date: dt.date, as_of: dt.date, grace_days: int) -> bool:
    """The late fee is added when the instalment is still unpaid after the grace period ends."""
    return as_of > due_date + dt.timedelta(days=grace_days)


def late_fee(rule_type: str, value: Decimal, cap: Decimal | None, principal_interest: Decimal) -> Decimal:
    """`flat`: `value`; `pct`: `value`% of the overdue principal + interest, capped at `cap`."""
    if rule_type == "flat":
        fee = Decimal(value)
    elif rule_type == "pct":
        fee = principal_interest * Decimal(value) / Decimal(100)
    else:
        raise ValueError(f"unknown late fee type {rule_type!r}")
    if cap is not None:
        fee = min(fee, Decimal(cap))
    return money(fee)


def amortize(principal: Decimal, apr: Decimal, months: int) -> list[tuple[Decimal, Decimal]]:
    """Equal-payment schedule: [(principal_due, interest_due)] per month; principal sums exactly."""
    if months < 1:
        raise ValueError("months must be >= 1")
    rate = Decimal(apr) / Decimal(1200)
    payment = principal / months if rate == 0 else principal * rate / (1 - (1 + rate) ** -months)
    balance = money(principal)
    rows: list[tuple[Decimal, Decimal]] = []
    for n in range(1, months + 1):
        interest = money(balance * rate)
        part = balance if n == months else min(balance, money(payment - interest))
        rows.append((money(part), interest))
        balance = money(balance - part)
    return rows


def add_months(day: dt.date, months: int) -> dt.date:
    """Same day of month `months` later (clamped to the month's last day)."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return dt.date(year, month + 1, min(day.day, last))


def schedule_status(inst: InstallmentLike, as_of: dt.date) -> str:
    """The stored `installments.status` for a row at `as_of`."""
    if not is_unpaid(inst):
        return "paid"
    if inst.paid_amount > ZERO:
        return "partial"
    return "missed" if inst.due_date < as_of else "scheduled"


def summary(insts: Sequence[InstallmentLike], as_of: dt.date) -> dict[str, object]:
    """Everything `LoanStatus` needs from the schedule."""
    dpd = days_past_due(insts, as_of)
    upcoming = next_due(insts, as_of)
    return {
        "outstanding_balance": outstanding(insts),
        "overdue_amount": overdue_amount(insts, as_of),
        "oldest_unpaid_due_date": oldest_unpaid_due_date(insts, as_of),
        "next_due_date": upcoming[0] if upcoming else None,
        "next_due_amount": upcoming[1] if upcoming else None,
        "days_past_due": dpd,
        "bucket": bucket(dpd),
        "fees_accrued": fees_accrued(insts),
    }
