"""T-GW-06 · GW-R4, GW-R5: verified read tools return the bound customer's exact figures, masked."""

from typing import Any

import pytest

from supportagent.gateway import session as session_mod
from supportagent.gateway.masking import assert_no_pii, speakable_date, speakable_money
from supportagent.gateway.session import Session
from supportagent.gateway.tools import Gateway, run_tool
from tests.gateway.conftest import open_session
from tests.gateway.fakes import FRANK, HANA, PRIYA, FakeBank, seeded_numbers

READ_TOOLS = ["get_loan_status", "get_recent_transactions", "get_interaction_history"]


async def verified(gateway: Gateway, persona: Any) -> Session:
    s = await open_session(gateway, loan_id=persona.loan_id)
    session_mod.bind_customer(s, persona.customer_id)  # identity is tested in test_identity_tools
    return s


@pytest.mark.parametrize("persona", [FRANK, HANA, PRIYA], ids=lambda p: p.name)
async def test_loan_status_equals_the_bank_exactly(gateway: Gateway, bank: FakeBank, persona: Any) -> None:
    s = await verified(gateway, persona)
    r = await run_tool(gateway, "get_loan_status", s.token, {})
    assert r.ok and r.data is not None
    (loan,) = r.data.loans
    expected = bank.status_of(persona.loan_id)
    for field in ("next_due_amount", "next_due_date", "days_past_due", "overdue_amount",
                  "outstanding_balance", "fees_accrued", "oldest_unpaid_due_date"):  # fmt: skip
        assert getattr(loan, field) == getattr(expected, field), field
    assert loan.loan_masked == f"****{persona.loan_last4}"
    assert speakable_money(expected.overdue_amount) in loan.speakable
    assert expected.oldest_unpaid_due_date is not None
    assert speakable_date(expected.oldest_unpaid_due_date) in loan.speakable


async def test_frank_is_five_days_past_due(gateway: Gateway) -> None:
    s = await verified(gateway, FRANK)
    r = await run_tool(gateway, "get_loan_status", s.token, {"loan_last4": FRANK.loan_last4})
    assert r.data is not None and r.data.loans[0].days_past_due == 5


async def test_values_serialise_exactly(gateway: Gateway, bank: FakeBank) -> None:
    s = await verified(gateway, FRANK)
    r = await run_tool(gateway, "get_loan_status", s.token, {})
    data = r.model_dump(mode="json")["data"]["loans"][0]
    expected = bank.status_of(FRANK.loan_id).model_dump(mode="json")
    assert data["next_due_amount"] == expected["next_due_amount"]  # same Decimal string, e.g. "265.33"
    assert data["next_due_date"] == expected["next_due_date"]


async def test_priyas_pending_payment_is_flagged(gateway: Gateway) -> None:
    s = await verified(gateway, PRIYA)
    r = await run_tool(gateway, "get_recent_transactions", s.token, {})
    assert r.data is not None
    assert 1 <= len(r.data.transactions) <= 5
    newest = r.data.transactions[0]
    assert newest.status == "pending" and "pending" in newest.speakable
    dates = [t.date for t in r.data.transactions]
    assert dates == sorted(dates, reverse=True)


async def test_interaction_history_is_structured_only(gateway: Gateway, bank: FakeBank) -> None:
    bianca = 9012
    s = await open_session(gateway, loan_id=bianca)
    session_mod.bind_customer(s, bianca)
    r = await run_tool(gateway, "get_interaction_history", s.token, {})
    assert r.data is not None
    assert [c.contact_result for c in r.data.recent_calls] == ["right_party"]
    assert [str(p.amount) for p in r.data.open_promises] == ["238.90"]
    dumped = r.model_dump_json()
    assert "summary" not in dumped and "short of funds" not in dumped


@pytest.mark.parametrize("tool", READ_TOOLS)
async def test_unverified_sessions_are_refused(gateway: Gateway, tool: str) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    r = await run_tool(gateway, tool, s.token, {})
    assert r.error is not None and r.error.code == "not_verified"


@pytest.mark.parametrize("tool", ["get_loan_status", "get_recent_transactions"])
async def test_foreign_last4_is_not_found(gateway: Gateway, tool: str) -> None:
    s = await verified(gateway, FRANK)
    r = await run_tool(gateway, tool, s.token, {"loan_last4": HANA.loan_last4})
    assert r.error is not None and r.error.code == "not_found"
    assert HANA.loan_last4 not in r.model_dump_json()


async def test_reads_never_touch_other_customers(gateway: Gateway, bank: FakeBank) -> None:
    s = await verified(gateway, FRANK)
    for tool in READ_TOOLS:
        await run_tool(gateway, tool, s.token, {})
    touched = {
        arg for name, arg in bank.calls if name in {"customer_loans", "loan_status", "interaction_history"}
    }
    assert touched == {FRANK.customer_id, FRANK.loan_id}  # loan id == customer id for personas


@pytest.mark.parametrize("persona", [FRANK, HANA, PRIYA], ids=lambda p: p.name)
@pytest.mark.parametrize("tool", READ_TOOLS)
async def test_no_pii_in_any_read_output(gateway: Gateway, bank: FakeBank, persona: Any, tool: str) -> None:
    s = await verified(gateway, persona)
    r = await run_tool(gateway, tool, s.token, {})
    assert r.ok
    assert_no_pii(r, seeded_numbers(bank))
    dumped = r.model_dump_json()
    full = bank.customers[persona.customer_id]
    assert (
        full.full_name.split()[-1] not in dumped
        and full.email not in dumped
        and full.dob.isoformat() not in dumped
    )
