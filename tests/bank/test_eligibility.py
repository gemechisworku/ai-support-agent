"""T-BANK-05 · BANK-R6: eligibility facts for the scheduler's compliance gate."""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from supportagent.bank import models
from supportagent.contracts import bank as c

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 11, 9, 14, tzinfo=dt.UTC)  # the frozen clock


def _eligible(api: TestClient, **params: object) -> dict[int, c.EligibleLoan]:
    r = api.get("/collections/eligible", params=params)
    assert r.status_code == 200, r.text
    return {x.loan_id: x for x in (c.EligibleLoan.model_validate(i) for i in r.json())}


def test_bucket_1_30_includes_the_collections_personas(api: TestClient) -> None:
    rows = _eligible(api, bucket="1-30")
    assert {9002, 9003, 9004, 9007} <= set(rows)  # Frank, Hana, Dawit, Sam
    assert 9001 not in rows and 9008 not in rows  # Olivia and Cleo are current
    assert all(1 <= r.days_past_due <= 30 and r.bucket == "1-30" for r in rows.values())
    frank = rows[9002]
    assert (frank.days_past_due, frank.phone, frank.timezone) == (5, "+15550100302", "America/New_York")
    assert frank.amount_due > Decimal(0)
    assert frank.prefs.consent_voice_ai is True and frank.prefs.cease_contact is False


def test_cease_contact_is_returned_as_is_not_filtered(api: TestClient, bank_db: Session) -> None:
    prefs = bank_db.get(models.ContactPreference, 9007)
    assert prefs is not None
    prefs.cease_contact = True
    bank_db.flush()
    sam = _eligible(api, bucket="1-30")[9007]
    assert sam.prefs.cease_contact is True


def test_due_within_3_includes_olivia(api: TestClient) -> None:
    rows = _eligible(api, due_within=3)
    olivia = rows[9001]
    assert olivia.next_due_date == dt.date(2026, 11, 11)
    assert (olivia.days_past_due, olivia.bucket) == (0, "current")
    assert olivia.amount_due > Decimal(0)
    assert 9008 not in rows  # Cleo is due in 12 days
    for r in rows.values():
        assert r.next_due_date is not None and dt.date(2026, 11, 9) <= r.next_due_date <= dt.date(
            2026, 11, 12
        )


def _call(
    bank_db: Session, *, days_ago: float, direction: str = "outbound", result: str = "no_answer"
) -> None:
    bank_db.add(
        models.Interaction(
            call_id=str(uuid.uuid4()),
            customer_id=9002,
            loan_id=9002,
            direction=direction,
            campaign="missed_payment" if direction == "outbound" else "inbound",
            started_at=NOW - dt.timedelta(days=days_ago),
            contact_result=result,
        )
    )


def test_attempts_and_last_conversation_come_from_interactions(api: TestClient, bank_db: Session) -> None:
    assert _eligible(api, bucket="1-30")[9002].attempts_last_7d == 0
    _call(bank_db, days_ago=1)
    _call(bank_db, days_ago=6.9, result="right_party")
    _call(bank_db, days_ago=8)  # outside the 7-day window
    _call(bank_db, days_ago=2, direction="inbound", result="right_party")  # not an attempt
    bank_db.flush()
    frank = _eligible(api, bucket="1-30")[9002]
    assert frank.attempts_last_7d == 2
    assert frank.last_conversation_at == NOW - dt.timedelta(days=2)


def test_bianca_last_conversation_is_the_seeded_promise_call(api: TestClient) -> None:
    bianca = _eligible(api, bucket="1-30")[9012]
    assert bianca.last_conversation_at is not None
    assert (NOW - bianca.last_conversation_at).days >= 7
    assert bianca.attempts_last_7d == 0


@pytest.mark.parametrize(
    "params", [{}, {"bucket": "1-30", "due_within": 3}, {"bucket": "current"}, {"due_within": -1}]
)
def test_exactly_one_filter_is_required(api: TestClient, params: dict[str, object]) -> None:
    assert api.get("/collections/eligible", params=params).status_code == 422
