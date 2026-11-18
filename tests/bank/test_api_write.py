"""T-BANK-06 · BANK-R7/R8: interaction-table writes, upsert, partial PATCH, outbox atomicity."""

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from supportagent.bank import models, queries
from supportagent.contracts import bank as c

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 11, 9, 14, tzinfo=dt.UTC)


def _attempt(call_id: str, **kw: Any) -> dict[str, Any]:
    return {
        "call_id": call_id,
        "customer_id": 9002,
        "loan_id": 9002,
        "direction": "outbound",
        "campaign": "missed_payment",
        "started_at": NOW.isoformat(),
        "contact_result": "no_answer",
        **kw,
    }


def _outcome(call_id: str, summary: str = "Promised to pay on 12 Nov.") -> dict[str, Any]:
    return {
        "call_id": call_id,
        "direction": "outbound",
        "campaign": "missed_payment",
        "customer_id": 9002,
        "loan_id": 9002,
        "contact_result": "right_party",
        "verified": True,
        "reason_code": "forgot",
        "promise_to_pay": {"date": "2026-11-12", "amount": "200.72"},
        "summary": summary,
        "kb_sources": ["late-fees-penalty-interest@1.0#late-fees-by-product"],
        "versions": {"prompt": "p1", "policy": "k1", "model": "llamacpp/llama-3.1-8b-instruct"},
        "transcript_uri": f"file:///var/transcripts/{call_id}.jsonl",
    }


def _count(s: Session, model: type[models.BankBase], **where: Any) -> int:
    stmt = select(func.count()).select_from(model)
    for k, v in where.items():
        stmt = stmt.where(getattr(model, k) == v)
    return int(s.scalar(stmt) or 0)


def test_attempt_then_outcome_upserts_one_row(api: TestClient, bank_db: Session) -> None:
    call_id = str(uuid.uuid4())
    first = api.post("/interactions", json=_attempt(call_id))
    assert first.status_code == 200, first.text
    assert first.json()["outcome"] is None
    final = _attempt(
        call_id,
        started_at=(NOW + dt.timedelta(seconds=5)).isoformat(),
        ended_at=(NOW + dt.timedelta(minutes=4)).isoformat(),
        contact_result="right_party",
        outcome=_outcome(call_id),
    )
    second = api.post("/interactions", json=final)
    assert second.status_code == 200, second.text
    row = c.Interaction.model_validate(second.json())
    assert _count(bank_db, models.Interaction, call_id=call_id) == 1
    assert row.contact_result == "right_party"
    assert row.started_at == NOW  # first write wins
    assert row.outcome is not None and row.outcome.reason_code == "forgot"
    assert row.versions is not None and row.versions.model == "llamacpp/llama-3.1-8b-instruct"
    assert row.transcript_uri == f"file:///var/transcripts/{call_id}.jsonl"
    # A later attempt-style write without an outcome does not erase it
    again = c.Interaction.model_validate(api.post("/interactions", json=_attempt(call_id)).json())
    assert again.outcome is not None


def test_outcome_with_summary_enqueues_exactly_one_sms(api: TestClient, bank_db: Session) -> None:
    call_id = str(uuid.uuid4())
    body = _attempt(call_id, contact_result="right_party", outcome=_outcome(call_id))
    api.post("/interactions", json=body)
    api.post("/interactions", json=body)  # re-post does not duplicate
    rows = bank_db.scalars(select(models.Outbox).where(models.Outbox.call_id == call_id)).all()
    assert [(r.channel, r.recipient, r.status) for r in rows] == [("sms", "+15550100302", "pending")]
    assert "Promised to pay on 12 Nov." in rows[0].body
    assert rows[0].body.startswith("Meridian Bank:")


def test_no_outbox_without_summary(api: TestClient, bank_db: Session) -> None:
    call_id = str(uuid.uuid4())
    api.post("/interactions", json=_attempt(call_id, outcome=_outcome(call_id, summary="")))
    api.post("/interactions", json=_attempt(str(uuid.uuid4())))
    assert _count(bank_db, models.Outbox, call_id=call_id) == 0


def test_outbox_failure_rolls_back_the_interaction(
    api: TestClient, bank_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("outbox down")

    monkeypatch.setattr(queries, "enqueue_summary", boom)
    call_id = str(uuid.uuid4())
    with pytest.raises(RuntimeError, match="outbox down"):
        api.post("/interactions", json=_attempt(call_id, outcome=_outcome(call_id)))
    assert _count(bank_db, models.Interaction, call_id=call_id) == 0


def test_mismatched_outcome_call_id_is_422(api: TestClient) -> None:
    call_id = str(uuid.uuid4())
    bad = _attempt(call_id, outcome=_outcome(str(uuid.uuid4())))
    assert api.post("/interactions", json=bad).status_code == 422


def test_create_promise_and_task(api: TestClient) -> None:
    promise = api.post(
        "/promises",
        json={"call_id": "c-1", "loan_id": 9002, "promised_date": "2026-11-12", "amount": "200.72"},
    )
    assert promise.status_code == 201, promise.text
    p = c.Promise.model_validate(promise.json())
    assert (p.status, str(p.amount)) == ("open", "200.72")
    task = api.post(
        "/tasks",
        json={
            "call_id": "c-2",
            "customer_id": 9003,
            "loan_id": 9003,
            "type": "escalation",
            "reason": "hardship",
            "summary": "Lost job; asked about hardship options.",
            "due_at": NOW.isoformat(),
        },
    )
    assert task.status_code == 201, task.text
    assert c.Task.model_validate(task.json()).status == "open"
    history = c.InteractionHistory.model_validate(api.get("/customers/9003/interactions").json())
    assert [t.reason for t in history.open_tasks] == ["hardship"]


def test_promise_and_task_for_unknown_rows_are_404(api: TestClient) -> None:
    assert (
        api.post(
            "/promises", json={"loan_id": 424242, "promised_date": "2026-11-12", "amount": "1"}
        ).status_code
        == 404
    )
    task = {"customer_id": 424242, "type": "callback", "reason": "x", "due_at": NOW.isoformat()}
    assert api.post("/tasks", json=task).status_code == 404


def test_patch_sets_only_given_flags_and_bumps_updated_at(api: TestClient, bank_db: Session) -> None:
    before = bank_db.get(models.ContactPreference, 9007)
    assert before is not None
    old_updated = before.updated_at
    r = api.patch("/contact-preferences/9007", json={"cease_contact": True})
    assert r.status_code == 200, r.text
    prefs = c.ContactPreferences.model_validate(r.json())
    assert (prefs.cease_contact, prefs.dnc, prefs.consent_voice_ai, prefs.dispute_open) == (
        True,
        False,
        True,
        False,
    )
    assert prefs.updated_at == NOW and prefs.updated_at > old_updated
    window = c.ContactPreferences.model_validate(
        api.patch("/contact-preferences/9007", json={"preferred_window": "18:00-20:00"}).json()
    )
    assert (window.cease_contact, window.preferred_window) == (True, "18:00-20:00")


@pytest.mark.parametrize("body", [{}, {"balance": 0}, {"preferred_window": "evening"}])
def test_bad_patch_is_422(api: TestClient, body: dict[str, Any]) -> None:
    assert api.patch("/contact-preferences/9007", json=body).status_code == 422


def test_patch_unknown_customer_is_404(api: TestClient) -> None:
    assert api.patch("/contact-preferences/424242", json={"dnc": True}).status_code == 404


def test_audit_is_append_only_204(api: TestClient, bank_db: Session) -> None:
    row = {
        "ts": NOW.isoformat(),
        "call_id": "service",
        "tool": "get_candidates",
        "args_hash": "a" * 64,
        "decision": "allow",
        "latency_ms": 3,
    }
    assert api.post("/audit", json=row).status_code == 204
    assert api.post("/audit", json=row).status_code == 204
    assert _count(bank_db, models.AuditLog, call_id="service") == 2
