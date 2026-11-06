"""T-BANK-01: bank API contracts round-trip to JSON, keep money as 2-dp strings, reject naive datetimes."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from supportagent.contracts import bank as c

CALL_ID = "3f2b8c1e-5d4a-4b6f-9a7e-2c1d0e9f8a7b"
NOW = datetime(2026, 11, 9, 14, tzinfo=UTC)
PREFS = c.ContactPreferences(
    customer_id=9002,
    consent_voice_ai=True,
    dnc=False,
    preferred_window="18:00-20:00",
    cease_contact=False,
    dispute_open=False,
    updated_at=NOW,
)

EXAMPLES: list[BaseModel] = [
    c.LoanProduct(
        code="AUTO",
        name="Auto Loan",
        rate_type="fixed",
        apr_min=Decimal("5.49"),
        apr_max=Decimal("14.99"),
        term_min=24,
        term_max=72,
        grace_days=10,
        late_fee_rule=c.LateFeeRule(type="pct", value=Decimal("5"), cap=Decimal("50")),
        policy_doc_id="product-auto-loan",
    ),
    c.Customer(
        id=9002,
        full_name="Frank Forgetful",
        dob=date(1985, 3, 14),
        phone="+15550100302",
        email="frank@example.com",
        address="1 Test St",
        timezone="America/New_York",
        segment="retail",
        kyc_status="verified",
    ),
    c.Loan(
        id=9002,
        customer_id=9002,
        product_code="PERSONAL",
        number="5520264417",
        principal=Decimal("6000"),
        apr=Decimal("12.50"),
        term_months=36,
        start_date=date(2025, 6, 4),
        status="active",
        days_past_due=5,
        bucket="1-30",
        repayment_account_id=9002,
    ),
    c.LoanStatus(
        loan_id=9002,
        customer_id=9002,
        number_last4="4417",
        product_code="PERSONAL",
        product_name="Personal Loan",
        as_of=date(2026, 11, 9),
        outstanding_balance=Decimal("4100.5"),
        overdue_amount=Decimal("200.72"),
        oldest_unpaid_due_date=date(2026, 11, 4),
        next_due_date=date(2026, 12, 4),
        next_due_amount=Decimal("200.72"),
        days_past_due=5,
        bucket="1-30",
        fees_accrued=Decimal("0"),
        status="active",
    ),
    c.Transaction(
        id=1,
        account_id=9005,
        loan_id=9005,
        ts=NOW,
        amount=Decimal("150"),
        channel="transfer",
        status="pending",
        reference="TRF-1",
    ),
    PREFS,
    c.EligibleLoan(
        loan_id=9002,
        customer_id=9002,
        product_code="PERSONAL",
        timezone="America/New_York",
        phone="+15550100302",
        days_past_due=5,
        bucket="1-30",
        next_due_date=date(2026, 12, 4),
        amount_due=Decimal("200.72"),
        prefs=PREFS,
        attempts_last_7d=0,
        last_conversation_at=None,
    ),
    c.InteractionIn(
        call_id=CALL_ID,
        customer_id=9002,
        loan_id=9002,
        direction="outbound",
        campaign="missed_payment",
        started_at=NOW,
        contact_result="no_answer",
    ),
    c.PromiseIn(call_id=CALL_ID, loan_id=9002, promised_date=date(2026, 11, 12), amount=Decimal("200.72")),
    c.TaskIn(customer_id=9003, type="escalation", reason="hardship", due_at=NOW),
    c.AuditIn(
        ts=NOW, call_id=CALL_ID, tool="get_loan_status", args_hash="0" * 64, decision="allow", latency_ms=12
    ),
    c.Page[c.Promise](items=[], total=0, limit=50, offset=0),
]


@pytest.mark.parametrize("model", EXAMPLES, ids=lambda m: type(m).__name__)
def test_json_round_trip(model: BaseModel) -> None:
    again = type(model).model_validate_json(model.model_dump_json())
    assert again == model


def test_money_is_a_two_dp_string() -> None:
    status = next(m for m in EXAMPLES if isinstance(m, c.LoanStatus))
    data = json.loads(status.model_dump_json())
    assert data["outstanding_balance"] == "4100.50"
    assert data["fees_accrued"] == "0.00"


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        c.TaskIn(customer_id=1, type="callback", reason="x", due_at=datetime(2026, 11, 9, 14))


@pytest.mark.parametrize("phone", ["5550100302", "+0123", "+1 555 010 0302"])
def test_phone_must_be_e164(phone: str) -> None:
    with pytest.raises(ValidationError):
        c.Customer.model_validate({**EXAMPLES[1].model_dump(), "phone": phone})


def test_interaction_outcome_is_a_call_outcome_with_matching_call_id() -> None:
    outcome = {
        "call_id": CALL_ID,
        "direction": "outbound",
        "contact_result": "no_answer",
        "versions": {"prompt": "p", "policy": "k", "model": "m"},
    }
    base = EXAMPLES[7].model_dump()
    ok = c.InteractionIn.model_validate({**base, "outcome": outcome})
    assert ok.outcome is not None and ok.outcome.call_id == CALL_ID
    with pytest.raises(ValidationError, match="call_id"):
        c.InteractionIn.model_validate(
            {**base, "outcome": {**outcome, "call_id": "6f2b8c1e-5d4a-4b6f-9a7e-2c1d0e9f8a7b"}}
        )


def test_patch_requires_a_field_and_validates_window() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        c.ContactPreferencesPatch()
    assert c.ContactPreferencesPatch(dnc=True).model_fields_set == {"dnc"}
    with pytest.raises(ValidationError):
        c.ContactPreferencesPatch(preferred_window="evening")


def test_promise_amount_positive_and_audit_hash_hex() -> None:
    with pytest.raises(ValidationError):
        c.PromiseIn(loan_id=1, promised_date=date(2026, 11, 12), amount=Decimal("0"))
    with pytest.raises(ValidationError):
        c.AuditIn(ts=NOW, call_id="service", tool="t", args_hash="xyz", decision="allow", latency_ms=0)
