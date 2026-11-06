"""T-PLAT-04 · PLAT-R4: CallOutcome (PRD §8) and the shared enums."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from supportagent.contracts import export
from supportagent.contracts.money import Money
from supportagent.contracts.outcome import (
    Callback,
    CallOutcome,
    Campaign,
    ContactResult,
    PromiseToPay,
    ReasonCode,
    Sentiment,
)

ROOT = Path(__file__).resolve().parents[2]
CALL_ID = "3f2b8c1e-5d4a-4b6f-9a7e-2c1d0e9f8a7b"

# Every field named in PRD §8 (latency_p50/p90 spelled out), plus the documented additions.
PRD_FIELDS = {
    "call_id", "direction", "campaign", "customer_id", "loan_id", "contact_result", "verified",
    "disclosures_given", "intent", "reason_code", "promise_to_pay", "callback", "escalated",
    "dispute_flag", "cease_contact_flag", "sentiment", "summary", "kb_sources", "versions",
    "latency_p50", "latency_p90", "recording_uri",
}  # fmt: skip
ADDITIONS = {"latency_stages", "transcript_uri"}


def outcome(**overrides: Any) -> dict[str, Any]:
    """A complete missed-payment outcome (Forgetful Frank) as JSON-like data."""
    base: dict[str, Any] = {
        "call_id": CALL_ID,
        "direction": "outbound",
        "campaign": "missed_payment",
        "customer_id": 9002,
        "loan_id": 90021,
        "contact_result": "right_party",
        "verified": True,
        "disclosures_given": ["ai_recording@1", "collections@1"],
        "intent": ["promise_to_pay"],
        "reason_code": "forgot",
        "promise_to_pay": {"date": "2026-11-12", "amount": "245.50"},
        "callback": None,
        "escalated": None,
        "dispute_flag": False,
        "cease_contact_flag": False,
        "sentiment": "positive",
        "summary": "Customer forgot; promised to pay on 12 Nov.",
        "kb_sources": ["late-fees@2#personal-loan"],
        "versions": {"prompt": "p-1a2b", "policy": "k-3c4d", "model": "ollama/qwen3:8b"},
        "latency_p50": 1650,
        "latency_p90": 2400,
        "latency_stages": {"stt": {"p50_ms": 300, "p90_ms": 450}},
        "recording_uri": "file:///var/recordings/3f2b8c1e.wav",
        "transcript_uri": "file:///var/transcripts/3f2b8c1e.jsonl",
    }
    base.update(overrides)
    return base


def test_every_prd_field_is_present() -> None:
    assert set(CallOutcome.model_fields) == PRD_FIELDS | ADDITIONS


def test_round_trip_through_json() -> None:
    model = CallOutcome.model_validate(outcome())
    again = CallOutcome.model_validate_json(model.model_dump_json())
    assert again == model
    assert model.promise_to_pay is not None
    assert model.promise_to_pay.amount == Decimal("245.50")
    assert model.promise_to_pay.date == date(2026, 11, 12)
    assert model.reason_code is ReasonCode.forgot


def test_minimal_no_answer_outcome() -> None:
    model = CallOutcome.model_validate(
        {
            "call_id": CALL_ID,
            "direction": "outbound",
            "campaign": "pre_due",
            "contact_result": "no_answer",
            "versions": {"prompt": "p", "policy": "k", "model": "m"},
        }
    )
    assert model.verified is False
    assert model.disclosures_given == []
    assert model.sentiment is Sentiment.unknown


def test_enum_values_match_prd() -> None:
    assert {c.value for c in ContactResult} == {
        "right_party", "third_party", "no_answer", "voicemail", "wrong_number", "busy",
    }  # fmt: skip
    assert {r.value for r in ReasonCode} == {
        "forgot", "insufficient_funds", "income_disruption", "dispute", "other",
    }  # fmt: skip
    assert "playground" in {c.value for c in Campaign}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contact_result", "answered"),
        ("reason_code", "lazy"),
        ("direction", "sideways"),
        ("campaign", "spam"),
        ("intent", ["chit_chat"]),
        ("sentiment", "ecstatic"),
    ],
)
def test_unknown_enum_values_are_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        CallOutcome.model_validate(outcome(**{field: value}))


@pytest.mark.parametrize("call_id", ["not-a-uuid", "3f2b8c1e-5d4a-1b6f-9a7e-2c1d0e9f8a7b"])  # v1 uuid
def test_call_id_must_be_uuid4(call_id: str) -> None:
    with pytest.raises(ValidationError, match="call_id"):
        CallOutcome.model_validate(outcome(call_id=call_id))


@pytest.mark.parametrize("amount", ["0", "0.00", "-5"])
def test_promise_amount_must_be_positive(amount: str) -> None:
    with pytest.raises(ValidationError):
        PromiseToPay.model_validate({"date": "2026-11-12", "amount": amount})


def test_promise_requires_date_and_amount() -> None:
    with pytest.raises(ValidationError):
        PromiseToPay.model_validate({"amount": "10"})
    with pytest.raises(ValidationError):
        PromiseToPay.model_validate({"date": "2026-11-12"})


def test_callback_datetime_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        Callback.model_validate({"datetime": "2026-11-12T10:00:00"})
    ok = Callback.model_validate({"datetime": "2026-11-12T10:00:00+03:00"})
    assert ok.datetime.utcoffset() is not None
    assert Callback(datetime=datetime(2026, 11, 12, tzinfo=UTC)).datetime.tzinfo is UTC


def test_verified_requires_right_party() -> None:
    with pytest.raises(ValidationError, match="right_party"):
        CallOutcome.model_validate(outcome(contact_result="third_party"))


def test_promise_requires_verification() -> None:
    with pytest.raises(ValidationError, match="verified"):
        CallOutcome.model_validate(outcome(verified=False))


def test_latency_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        CallOutcome.model_validate(outcome(latency_p50=-1))


class _Price(BaseModel):
    amount: Money


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("10", "10.00"), (10, "10.00"), ("245.5", "245.50"), (245.5, "245.50"), (Decimal("0.01"), "0.01")],
)
def test_money_normalises_to_two_decimals_and_serialises_as_string(raw: Any, expected: str) -> None:
    price = _Price(amount=raw)
    assert price.amount == Decimal(expected)
    assert json.loads(price.model_dump_json()) == {"amount": expected}


@pytest.mark.parametrize("raw", [10.005, "10.005", Decimal("1.234"), -1, "abc", 10**11])
def test_money_rejects_bad_values(raw: Any) -> None:
    with pytest.raises(ValidationError):
        _Price(amount=raw)


def test_schema_export_is_current() -> None:
    path = ROOT / export.SCHEMA_PATH
    assert path.exists(), "run `uv run python -m supportagent.contracts.export`"
    assert path.read_text() == export.render(), "stale: run `uv run python -m supportagent.contracts.export`"


def test_export_check_mode(tmp_path: Path) -> None:
    target = tmp_path / "schema.json"
    assert export.main(["--path", str(target), "--check"]) == 1  # missing → stale
    assert export.main(["--path", str(target)]) == 0
    assert export.main(["--path", str(target), "--check"]) == 0
