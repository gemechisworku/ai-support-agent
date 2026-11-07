"""T-BANK-02 · BANK-R5: the pure ledger. Property checks run over many random schedules (seeded)."""

import datetime as dt
import random
from decimal import Decimal

import pytest

from supportagent.bank import ledger
from supportagent.bank.ledger import Line

AS_OF = dt.date(2026, 11, 9)


def _random_schedule(rng: random.Random) -> list[Line]:
    start = AS_OF - dt.timedelta(days=rng.randint(-60, 900))
    lines = []
    for seq in range(rng.randint(1, 36)):
        p = Decimal(rng.randint(100, 50_000)) / 100
        i = Decimal(rng.randint(0, 5_000)) / 100
        fee = Decimal(rng.choice([0, 0, 1000, 2500])) / 100
        total = p + i + fee
        paid = rng.choice([Decimal(0), total, (total / 2).quantize(ledger.CENT)])
        lines.append(Line(ledger.add_months(start, seq), p, i, fee, paid))
    return lines


@pytest.mark.parametrize("seed", range(200))
def test_properties_over_random_schedules(seed: int) -> None:
    rng = random.Random(seed)
    lines = _random_schedule(rng)
    unpaid = [x for x in lines if x.principal_due + x.interest_due + x.fee_due - x.paid_amount > 0]
    expected = sum((x.principal_due + x.interest_due + x.fee_due - x.paid_amount for x in unpaid), Decimal(0))
    assert ledger.outstanding(lines) == expected.quantize(ledger.CENT)

    late = sorted((x for x in unpaid if x.due_date < AS_OF), key=lambda x: x.due_date)
    dpd = ledger.days_past_due(lines, AS_OF)
    assert dpd == ((AS_OF - late[0].due_date).days if late else 0)
    assert ledger.overdue_amount(lines, AS_OF) <= ledger.outstanding(lines)
    assert ledger.bucket(dpd) == ledger.summary(lines, AS_OF)["bucket"]
    assert ledger.fees_accrued(lines) <= sum((x.fee_due for x in lines), Decimal(0))


def test_dpd_counts_from_the_day_after_the_due_date() -> None:
    line = Line(AS_OF, Decimal("100"), Decimal("5"))
    assert ledger.days_past_due([line], AS_OF) == 0  # due today: not yet past due
    assert ledger.days_past_due([line], AS_OF + dt.timedelta(days=1)) == 1
    assert ledger.days_past_due([line], AS_OF + dt.timedelta(days=5)) == 5


def test_grace_period_changes_the_fee_not_the_dpd() -> None:
    due = AS_OF - dt.timedelta(days=15)
    assert ledger.days_past_due([Line(due, Decimal("100"), Decimal("5"))], AS_OF) == 15
    assert not ledger.fee_applies(due, AS_OF, grace_days=15)  # last day of grace
    assert ledger.fee_applies(due, AS_OF + dt.timedelta(days=1), grace_days=15)


def test_partially_paid_instalment_is_still_past_due() -> None:
    line = Line(AS_OF - dt.timedelta(days=22), Decimal("300"), Decimal("20"), paid_amount=Decimal("100"))
    assert ledger.days_past_due([line], AS_OF) == 22
    assert ledger.overdue_amount([line], AS_OF) == Decimal("220.00")
    assert ledger.schedule_status(line, AS_OF) == "partial"


@pytest.mark.parametrize(
    ("dpd", "bucket"),
    [
        (0, "current"),
        (1, "1-30"),
        (30, "1-30"),
        (31, "31-60"),
        (60, "31-60"),
        (61, "61-90"),
        (90, "61-90"),
        (91, "90+"),
    ],
)
def test_bucket_boundaries(dpd: int, bucket: str) -> None:
    assert ledger.bucket(dpd) == bucket


@pytest.mark.parametrize(
    ("rule", "value", "cap", "base", "fee"),
    [
        ("flat", "25", None, "500", "25.00"),
        ("pct", "5", "50", "400", "20.00"),
        ("pct", "5", "50", "2000", "50.00"),  # capped
        ("pct", "4", None, "1234.56", "49.38"),
    ],
)
def test_late_fee_rules(rule: str, value: str, cap: str | None, base: str, fee: str) -> None:
    assert ledger.late_fee(rule, Decimal(value), Decimal(cap) if cap else None, Decimal(base)) == Decimal(fee)


@pytest.mark.parametrize(
    ("principal", "apr", "months"), [("6000", "12.50", 36), ("500", "0", 3), ("1200", "35.99", 1)]
)
def test_amortize_repays_the_principal_exactly(principal: str, apr: str, months: int) -> None:
    rows = ledger.amortize(Decimal(principal), Decimal(apr), months)
    assert len(rows) == months
    assert sum(p for p, _ in rows) == Decimal(principal)
    assert all(p >= 0 and i >= 0 for p, i in rows)


def test_next_due_skips_paid_and_past() -> None:
    lines = [
        Line(AS_OF - dt.timedelta(days=30), Decimal("10"), Decimal("1")),
        Line(AS_OF + dt.timedelta(days=1), Decimal("10"), Decimal("1"), paid_amount=Decimal("11")),
        Line(AS_OF + dt.timedelta(days=31), Decimal("10"), Decimal("1")),
    ]
    assert ledger.next_due(lines, AS_OF) == (AS_OF + dt.timedelta(days=31), Decimal("11.00"))


def test_add_months_clamps_to_month_end() -> None:
    assert ledger.add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)
    assert ledger.add_months(dt.date(2026, 3, 15), -3) == dt.date(2025, 12, 15)
