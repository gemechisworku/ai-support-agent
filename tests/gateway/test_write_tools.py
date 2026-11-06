"""T-GW-07 · GW-R6, GW-R10: write tools touch interaction tables only and enforce the business rules."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest

from supportagent.contracts.outcome import CallOutcome
from supportagent.gateway import session as session_mod
from supportagent.gateway.session import Session
from supportagent.gateway.tools import Gateway, run_tool
from tests.gateway.conftest import open_session
from tests.gateway.fakes import AS_OF, FRANK, HANA, FakeBank


async def frank(gateway: Gateway, **kw: Any) -> Session:
    s = await open_session(gateway, loan_id=FRANK.loan_id, **kw)
    session_mod.bind_customer(s, FRANK.customer_id)
    return s


def promise(date: dt.date, amount: Decimal | str) -> dict[str, str]:
    return {"promise_date": date.isoformat(), "amount": str(amount)}


@pytest.mark.parametrize(
    ("days", "reason"), [(0, "ok"), (14, "ok"), (15, "date_out_of_window"), (-1, "date_out_of_window")]
)
async def test_promise_date_window(gateway: Gateway, bank: FakeBank, days: int, reason: str) -> None:
    s = await frank(gateway)
    due = bank.status_of(FRANK.loan_id).overdue_amount
    r = await run_tool(
        gateway, "record_promise_to_pay", s.token, promise(AS_OF + dt.timedelta(days=days), due)
    )
    assert r.data is not None
    assert r.data.reason == reason and r.data.accepted is (reason == "ok")
    assert r.data.max_date == AS_OF + dt.timedelta(days=14) and r.data.amount_due == due


@pytest.mark.parametrize(
    ("delta", "reason"), [("-due", "amount_invalid"), ("0", "ok"), ("0.01", "amount_invalid")]
)
async def test_promise_amount_bounds(gateway: Gateway, bank: FakeBank, delta: str, reason: str) -> None:
    s = await frank(gateway)
    due = bank.status_of(FRANK.loan_id).overdue_amount
    amount = Decimal("0") if delta == "-due" else due + Decimal(delta)
    r = await run_tool(
        gateway, "record_promise_to_pay", s.token, promise(AS_OF + dt.timedelta(days=3), amount)
    )
    assert r.data is not None and r.data.reason == reason


async def test_partial_promise_allowed_and_posted(gateway: Gateway, bank: FakeBank) -> None:
    s = await frank(gateway)
    r = await run_tool(gateway, "record_promise_to_pay", s.token, promise(AS_OF + dt.timedelta(days=3), "50"))
    assert r.data is not None and r.data.accepted and r.data.promise_id is not None
    posted = bank.promises[-1]
    assert (posted.loan_id, posted.amount, posted.call_id) == (FRANK.loan_id, Decimal("50.00"), s.call_id)


async def test_promise_for_a_foreign_loan(gateway: Gateway) -> None:
    s = await frank(gateway)
    body = {**promise(AS_OF, "10"), "loan_last4": HANA.loan_last4}
    r = await run_tool(gateway, "record_promise_to_pay", s.token, body)
    assert r.data is not None and r.data.reason == "loan_not_found" and not r.data.accepted


async def test_callback_and_escalation_tasks(gateway: Gateway, bank: FakeBank) -> None:
    s = await frank(gateway)
    when = dt.datetime(2026, 11, 10, 18, tzinfo=dt.UTC)
    r = await run_tool(
        gateway, "request_callback", s.token, {"preferred_at": when.isoformat(), "reason": "after work"}
    )
    assert r.data is not None and r.data.type == "callback" and r.data.due_at == when
    r = await run_tool(
        gateway, "escalate", s.token, {"reason": "hardship", "summary": "Lost job; wants options."}
    )
    assert r.data is not None
    assert r.data.model_dump() == {
        "task_id": bank.tasks[-1].id,
        "human_available": True,
        "transfer_extension": "200",
    }
    assert [(t.type, t.customer_id, t.reason) for t in bank.tasks] == [
        ("callback", FRANK.customer_id, "after work"),
        ("escalation", FRANK.customer_id, "hardship"),
    ]


async def test_callback_time_must_be_in_the_next_30_days(gateway: Gateway) -> None:
    s = await frank(gateway)
    for when in ("2026-11-09T13:00:00+00:00", "2026-12-31T10:00:00+00:00"):
        r = await run_tool(gateway, "request_callback", s.token, {"preferred_at": when, "reason": "x"})
        assert r.error is not None and r.error.code == "invalid_input"


async def test_escalate_works_unverified_and_locked(gateway: Gateway, bank: FakeBank) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    s.locked = True
    r = await run_tool(
        gateway, "escalate", s.token, {"reason": "verification_failed", "summary": "Two failures."}
    )
    assert r.data is not None and r.data.task_id is not None
    assert bank.tasks[-1].customer_id == FRANK.customer_id  # the customer the call was about


async def test_escalate_without_any_known_customer(gateway: Gateway, bank: FakeBank) -> None:
    s = await open_session(gateway, direction="inbound", campaign="inbound", caller_phone="+15559990000")
    r = await run_tool(
        gateway, "escalate", s.token, {"reason": "human_requested", "summary": "Wants a person."}
    )
    assert r.data is not None and r.data.task_id is None and r.data.transfer_extension == "200"
    assert bank.tasks == []


async def test_human_unavailable(gateway: Gateway) -> None:
    from dataclasses import replace

    gateway.config = replace(gateway.config, human_agent_available=False)
    s = await frank(gateway)
    r = await run_tool(gateway, "escalate", s.token, {"reason": "complaint", "summary": "Unhappy."})
    assert r.data is not None and r.data.human_available is False and r.data.transfer_extension is None


@pytest.mark.parametrize(
    ("flag", "extra", "field", "value"),
    [("dispute", {}, "dispute_open", True), ("cease_contact", {}, "cease_contact", True),
     ("preferred_window", {"preferred_window": "18:00-20:00"}, "preferred_window", "18:00-20:00")],
)  # fmt: skip
async def test_contact_flags(
    gateway: Gateway, bank: FakeBank, flag: str, extra: dict[str, str], field: str, value: object
) -> None:
    s = await frank(gateway)
    r = await run_tool(gateway, "set_contact_flag", s.token, {"flag": flag, **extra})
    assert r.data is not None and r.data.applied
    assert getattr(bank.prefs[FRANK.customer_id], field) == value


async def test_contact_flag_needs_verification(gateway: Gateway) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    r = await run_tool(gateway, "set_contact_flag", s.token, {"flag": "cease_contact"})
    assert r.error is not None and r.error.code == "not_verified"


def outcome(s: Session, **kw: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "call_id": s.call_id, "direction": "outbound", "campaign": "missed_payment",
        "customer_id": FRANK.customer_id, "loan_id": FRANK.loan_id, "contact_result": "right_party",
        "verified": True, "kb_sources": ["faq@1.0#Paying"],
        "versions": {"prompt": "p1", "policy": "k1", "model": "m1"},
    }  # fmt: skip
    data.update(kw)
    return CallOutcome.model_validate(data).model_dump(mode="json")


async def test_outcome_merges_sources_and_closes_the_session(gateway: Gateway, bank: FakeBank) -> None:
    s = await frank(gateway)
    s.record_source("late-fees-penalty-interest@1.0#Late fees > Personal loan")
    r = await run_tool(gateway, "record_call_outcome", s.token, outcome(s))
    assert r.data is not None and r.data.interaction_id == s.call_id
    stored = bank.interactions[s.call_id]
    assert stored.outcome is not None
    assert stored.outcome.kb_sources == [
        "faq@1.0#Paying",
        "late-fees-penalty-interest@1.0#Late fees > Personal loan",
    ]
    for tool, args in [("get_loan_status", {}), ("record_call_outcome", outcome(s)), ("escalate", {})]:
        again = await run_tool(gateway, tool, s.token, args)
        assert again.error is not None and again.error.code == "session_closed"


async def test_outcome_cannot_name_another_call_or_customer(gateway: Gateway) -> None:
    s = await frank(gateway)
    other_call = await run_tool(
        gateway, "record_call_outcome", s.token, outcome(s, call_id=str(uuid.uuid4()))
    )
    assert other_call.error is not None and other_call.error.code == "invalid_input"
    other_customer = await run_tool(
        gateway, "record_call_outcome", s.token, outcome(s, customer_id=HANA.customer_id, loan_id=None)
    )
    assert other_customer.error is not None and other_customer.error.code == "forbidden"
    assert not s.closed


async def test_no_write_targets_the_ledger(gateway: Gateway, bank: FakeBank) -> None:
    s = await frank(gateway)
    await run_tool(gateway, "record_promise_to_pay", s.token, promise(AS_OF, "10"))
    await run_tool(gateway, "set_contact_flag", s.token, {"flag": "dispute"})
    await run_tool(gateway, "escalate", s.token, {"reason": "dispute", "summary": "Fee disputed."})
    await run_tool(gateway, "record_call_outcome", s.token, outcome(s))
    assert set(bank.writes()) == {
        "create_promise",
        "patch_contact_preferences",
        "create_task",
        "upsert_interaction",
    }
    assert not [name for name, _ in bank.calls if "balance" in name or "installment" in name]
