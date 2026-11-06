"""T-GW-01 · GW-R1, GW-R10: the gateway's tool contracts."""

import datetime as dt
import typing
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from supportagent.contracts import gateway as g

INPUTS = [m for m, _ in g.TOOL_MODELS.values()]
OUTPUTS = [m for _, m in g.TOOL_MODELS.values()]


def test_tool_table_matches_spec() -> None:
    assert set(g.TOOL_MODELS) == {
        "lookup_by_phone", "verify_identity", "search_policy", "get_loan_status",
        "get_recent_transactions", "get_interaction_history", "record_promise_to_pay",
        "request_callback", "escalate", "set_contact_flag",
    }  # fmt: skip


@pytest.mark.parametrize("model", INPUTS, ids=lambda m: m.__name__)
def test_no_input_accepts_a_customer_id(model: type[BaseModel]) -> None:
    assert "customer_id" not in model.model_fields
    assert model.model_config.get("extra") == "forbid"
    with pytest.raises(ValidationError):
        model.model_validate({"customer_id": 7})


FORBIDDEN_OUTPUT_FIELDS = {
    "surname",
    "last_name",
    "full_name",
    "dob",
    "address",
    "email",
    "national_id",
    "phone",
}


def _fields(model: type[BaseModel]) -> set[str]:
    names = set(model.model_fields)
    for f in model.model_fields.values():
        for arg in typing.get_args(f.annotation) or (f.annotation,):
            for inner in typing.get_args(arg) or (arg,):
                if isinstance(inner, type) and issubclass(inner, BaseModel):
                    names |= _fields(inner)
    return names


@pytest.mark.parametrize("model", OUTPUTS, ids=lambda m: m.__name__)
def test_outputs_have_no_identifying_fields(model: type[BaseModel]) -> None:
    assert not (_fields(model) & FORBIDDEN_OUTPUT_FIELDS)


@pytest.mark.parametrize("last4", ["441", "44170", "44a7", ""])
def test_loan_last4_must_be_four_digits(last4: str) -> None:
    with pytest.raises(ValidationError):
        g.VerifyIdentityInput(full_name="Frank Fischer", dob=dt.date(1985, 3, 14), loan_last4=last4)


def test_money_and_dates_are_typed() -> None:
    p = g.RecordPromiseInput.model_validate({"promise_date": "2026-11-12", "amount": "100.5"})
    assert p.amount == Decimal("100.50")
    assert p.promise_date == dt.date(2026, 11, 12)
    with pytest.raises(ValidationError):
        g.RequestCallbackInput.model_validate({"preferred_at": "2026-11-12T18:00:00", "reason": "x"})


def test_tool_result_round_trip_and_consistency() -> None:
    ok = g.ToolResult[g.ContactFlagOutput].success(g.ContactFlagOutput(flag="dispute", applied=True))
    again = g.ToolResult[g.ContactFlagOutput].model_validate_json(ok.model_dump_json())
    assert again == ok
    err = g.ToolResult[g.ContactFlagOutput].failure("not_verified")
    assert err.model_dump(mode="json") == {
        "ok": False,
        "data": None,
        "error": {"code": "not_verified", "message": ""},
    }
    with pytest.raises(ValidationError):
        g.ToolResult[g.ContactFlagOutput](ok=False)
    with pytest.raises(ValidationError):
        g.ToolError(code="teapot")  # type: ignore[arg-type]


def test_contact_flag_window_rule() -> None:
    g.SetContactFlagInput(flag="preferred_window", preferred_window="18:00-20:00")
    with pytest.raises(ValidationError):
        g.SetContactFlagInput(flag="preferred_window")
    with pytest.raises(ValidationError):
        g.SetContactFlagInput(flag="dispute", preferred_window="18:00-20:00")


def test_escalation_reasons() -> None:
    assert {r.value for r in g.EscalationReason} == {
        "dispute", "hardship", "complaint", "legal", "human_requested",
        "verification_failed", "misunderstanding", "other",
    }  # fmt: skip
