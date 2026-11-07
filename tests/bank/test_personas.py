"""T-BANK-03 · BANK-R3: each seeded persona is in exactly its situation at as_of (database)."""

import datetime as dt
from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from supportagent.bank import ledger
from supportagent.bank.seed import PERSONA_BY_ID, generate
from supportagent.bank.seed.writer import write

AS_OF = dt.date(2026, 11, 9)


def _schedule(s: Session, loan_id: int) -> list[ledger.Line]:
    rows = s.execute(
        text(
            "SELECT due_date, principal_due, interest_due, fee_due, paid_amount "
            "FROM installments WHERE loan_id = :l ORDER BY seq"
        ),
        {"l": loan_id},
    ).all()
    return [ledger.Line(*r) for r in rows]


def _status(s: Session, cid: int) -> dict[str, object]:
    return ledger.summary(_schedule(s, PERSONA_BY_ID[cid].loan_id), AS_OF)


def _scalar(s: Session, sql: str, **params: object) -> object:
    return s.execute(text(sql), params).scalar_one()


@pytest.fixture
def seeded(bank_db: Session) -> Session:
    write(bank_db, generate(42, AS_OF), reset_first=True)
    return bank_db


def _olivia(s: Session) -> None:
    st = _status(s, 9001)
    assert st["days_past_due"] == 0
    assert st["next_due_date"] == AS_OF + dt.timedelta(days=2)
    assert _scalar(s, "SELECT count(*) FROM installments WHERE loan_id=9001 AND paid_date > due_date") == 0


def _frank(s: Session) -> None:
    assert _status(s, 9002)["days_past_due"] == 5
    assert _scalar(s, "SELECT count(*) FROM installments WHERE loan_id=9002 AND status='missed'") == 1
    assert _scalar(s, "SELECT count(*) FROM installments WHERE loan_id=9002 AND paid_date > due_date") == 0
    assert _status(s, 9002)["fees_accrued"] == Decimal("0.00")  # still inside the 15-day grace


def _hana(s: Session) -> None:
    assert _status(s, 9003)["days_past_due"] == 22
    assert _status(s, 9003)["fees_accrued"] > 0  # AUTO grace is 10 days
    split = (
        "SELECT count(*) FROM transactions "
        "WHERE loan_id=9003 AND (reference LIKE '%a' OR reference LIKE '%b')"
    )
    assert _scalar(s, split) == 4  # two instalments paid in two parts each


def _dawit(s: Session) -> None:
    st = _status(s, 9004)
    assert st["days_past_due"] == 12
    assert st["fees_accrued"] == Decimal("75.00")  # SME_WC flat fee after 7 days' grace


def _priya(s: Session) -> None:
    st = _status(s, 9005)
    assert st["days_past_due"] == 3
    pending = _scalar(s, "SELECT amount FROM transactions WHERE loan_id=9005 AND status='pending'")
    assert pending == st["overdue_amount"]


def _dpd(cid: int, dpd: int) -> Callable[[Session], None]:
    def check(s: Session) -> None:
        assert _status(s, cid)["days_past_due"] == dpd

    return check


def _cleo(s: Session) -> None:
    st = _status(s, 9008)
    assert (st["days_past_due"], st["bucket"]) == (0, "current")
    assert st["next_due_date"] == AS_OF + dt.timedelta(days=12)


def _anton(s: Session) -> None:
    st = _status(s, 9010)
    assert st["days_past_due"] == 9
    assert st["fees_accrued"] == Decimal("10.00")


def _bianca(s: Session) -> None:
    assert _status(s, 9012)["days_past_due"] == 10
    row = s.execute(text("SELECT promised_date, status FROM promises_to_pay WHERE loan_id=9012")).one()
    assert tuple(row) == (AS_OF - dt.timedelta(days=1), "open")
    started = _scalar(s, "SELECT started_at FROM interactions WHERE customer_id=9012")
    assert (dt.datetime(2026, 11, 9, tzinfo=dt.UTC) - started).days >= 7  # type: ignore[operator]


CHECKS = {
    9001: _olivia,
    9002: _frank,
    9003: _hana,
    9004: _dawit,
    9005: _priya,
    9006: _dpd(9006, 8),
    9007: _dpd(9007, 15),
    9008: _cleo,
    9009: _dpd(9009, 6),
    9010: _anton,
    9011: _dpd(9011, 4),
    9012: _bianca,
}


@pytest.mark.integration
@pytest.mark.parametrize("cid", sorted(CHECKS), ids=lambda c: PERSONA_BY_ID[c].name)
def test_persona_situation(seeded: Session, cid: int) -> None:
    p = PERSONA_BY_ID[cid]
    found = seeded.execute(text("SELECT id, dob FROM customers WHERE phone = :p"), {"p": p.phone}).one()
    assert tuple(found) == (cid, p.dob)
    CHECKS[cid](seeded)
    cached = seeded.execute(
        text("SELECT days_past_due FROM loans WHERE id = :l"), {"l": p.loan_id}
    ).scalar_one()
    assert cached == p.days_past_due


def test_every_persona_has_a_check() -> None:
    assert set(CHECKS) == set(PERSONA_BY_ID)
