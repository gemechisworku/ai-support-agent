"""Builds one loan's rows (loan, instalment schedule, payment transactions) in a chosen situation.

A loan is placed relative to `as_of` by its **anchor**: the first unpaid instalment. Instalments before
the anchor are paid; from the anchor on they are unpaid (missed if due before `as_of`, with the late fee
once the grace period has passed). So a loan whose anchor is `as_of - D` is exactly D days past due, and
a current loan's anchor is its next due date. Shared by the population and the personas.
"""

import datetime as dt
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal

from supportagent.bank import ledger
from supportagent.contracts.bank import LoanProduct

UTC = dt.UTC
CHANNELS = ("branch", "mobile", "transfer")


@dataclass
class SeedData:
    """Rows per table, in insert order."""

    loan_products: list[dict[str, object]] = field(default_factory=list)
    customers: list[dict[str, object]] = field(default_factory=list)
    accounts: list[dict[str, object]] = field(default_factory=list)
    contact_preferences: list[dict[str, object]] = field(default_factory=list)
    loans: list[dict[str, object]] = field(default_factory=list)
    installments: list[dict[str, object]] = field(default_factory=list)
    transactions: list[dict[str, object]] = field(default_factory=list)
    promises_to_pay: list[dict[str, object]] = field(default_factory=list)

    def tables(self) -> Iterator[tuple[str, list[dict[str, object]]]]:
        yield from vars(self).items()

    def extend(self, other: "SeedData") -> None:
        for name, rows in other.tables():
            getattr(self, name).extend(rows)


@dataclass
class LoanPlan:
    loan_id: int
    customer_id: int
    account_id: int
    product: LoanProduct
    number: str
    principal: Decimal
    apr: Decimal
    term: int
    anchor: dt.date  # due date of the first unpaid instalment
    anchor_seq: int  # its sequence number (1-based)
    partial_paid: Decimal = ledger.ZERO  # paid towards the anchor instalment (status "partial")
    split_history: int = 0  # the last N paid instalments were paid in two transactions
    pending_payment: Decimal = ledger.ZERO  # a pending (not yet posted) transfer, ts = as_of - 1 day


def _ts(day: dt.date, rng: random.Random) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, rng.randint(8, 19), rng.randint(0, 59), tzinfo=UTC)


def build_loan(
    plan: LoanPlan, as_of: dt.date, rng: random.Random, tx_ids: Iterator[int]
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    rule = plan.product.late_fee_rule
    rows: list[dict[str, object]] = []
    txs: list[dict[str, object]] = []
    lines: list[ledger.Line] = []

    def pay(seq: int, amount: Decimal, day: dt.date, ref: str) -> None:
        txs.append(
            {
                "id": next(tx_ids),
                "account_id": plan.account_id,
                "loan_id": plan.loan_id,
                "ts": _ts(day, rng),
                "amount": ledger.money(amount),
                "channel": rng.choice(CHANNELS),
                "status": "posted",
                "reference": f"PMT-{plan.number}-{seq:03d}{ref}",
            }
        )

    for seq, (principal_due, interest_due) in enumerate(
        ledger.amortize(plan.principal, plan.apr, plan.term), start=1
    ):
        due = ledger.add_months(plan.anchor, seq - plan.anchor_seq)
        fee = ledger.ZERO
        paid = ledger.ZERO
        paid_date: dt.date | None = None
        if seq < plan.anchor_seq:
            paid = ledger.money(principal_due + interest_due)
            paid_date = min(due + dt.timedelta(days=rng.randint(-5, 2)), as_of - dt.timedelta(days=1))
            if seq >= plan.anchor_seq - plan.split_history:
                first = ledger.money(paid / 2)
                pay(seq, first, paid_date - dt.timedelta(days=3), "a")
                pay(seq, paid - first, paid_date, "b")
            else:
                pay(seq, paid, paid_date, "")
        else:
            if due < as_of and ledger.fee_applies(due, as_of, plan.product.grace_days):
                fee = ledger.late_fee(rule.type, rule.value, rule.cap, principal_due + interest_due)
            if seq == plan.anchor_seq and plan.partial_paid > ledger.ZERO:
                paid = ledger.money(plan.partial_paid)
                paid_date = min(due + dt.timedelta(days=1), as_of - dt.timedelta(days=1))
                pay(seq, paid, paid_date, "p")
        line = ledger.Line(due, principal_due, interest_due, fee, paid)
        lines.append(line)
        rows.append(
            {
                "loan_id": plan.loan_id,
                "seq": seq,
                "due_date": due,
                "principal_due": principal_due,
                "interest_due": interest_due,
                "fee_due": fee,
                "paid_amount": paid,
                "paid_date": paid_date,
                "status": ledger.schedule_status(line, as_of),
            }
        )

    if plan.pending_payment > ledger.ZERO:
        txs.append(
            {
                "id": next(tx_ids),
                "account_id": plan.account_id,
                "loan_id": plan.loan_id,
                "ts": _ts(as_of - dt.timedelta(days=1), rng),
                "amount": ledger.money(plan.pending_payment),
                "channel": "transfer",
                "status": "pending",
                "reference": f"PMT-{plan.number}-PENDING",
            }
        )

    dpd = ledger.days_past_due(lines, as_of)
    bucket = ledger.bucket(dpd)
    loan: dict[str, object] = {
        "id": plan.loan_id,
        "customer_id": plan.customer_id,
        "product_code": plan.product.code,
        "number": plan.number,
        "principal": ledger.money(plan.principal),
        "apr": plan.apr,
        "term_months": plan.term,
        "start_date": ledger.add_months(plan.anchor, -plan.anchor_seq),
        "status": "default" if bucket == "90+" else "active",
        "days_past_due": dpd,
        "bucket": bucket,
        "repayment_account_id": plan.account_id,
    }
    return loan, rows, txs
