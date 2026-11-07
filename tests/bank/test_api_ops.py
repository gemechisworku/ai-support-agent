"""T-BANK-07 · BANK-R9: operations read endpoints for the console (filters, pagination, ordering, detail)."""

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from supportagent.contracts import bank as c

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 11, 9, 14, tzinfo=dt.UTC)
SINCE = (NOW - dt.timedelta(hours=2)).isoformat()


def _call(
    started: dt.datetime,
    *,
    direction: str = "outbound",
    campaign: str | None = "missed_payment",
    contact_result: str = "no_answer",
    outcome: dict[str, Any] | None = None,
    call_id: str | None = None,
) -> dict[str, Any]:
    call_id = call_id or str(uuid.uuid4())
    body: dict[str, Any] = {
        "call_id": call_id,
        "customer_id": 9002,
        "loan_id": 9002,
        "direction": direction,
        "campaign": campaign,
        "started_at": started.isoformat(),
        "contact_result": contact_result,
    }
    if outcome is not None:
        body["outcome"] = {"call_id": call_id, **outcome}
    return body


def _outcome(direction: str, campaign: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "direction": direction,
        "campaign": campaign,
        "customer_id": 9002,
        "loan_id": 9002,
        "contact_result": "right_party",
        "verified": True,
        "summary": "Talked to Frank.",
        "versions": {"prompt": "p1", "policy": "k1", "model": "ollama/qwen3:8b"},
        **extra,
    }


def _seed_calls(api: TestClient) -> dict[str, str]:
    """A: missed-payment no-answer · B: pre-due escalated · C: inbound right party (newest)."""
    a = _call(NOW - dt.timedelta(hours=1))
    b = _call(
        NOW - dt.timedelta(minutes=30),
        campaign="pre_due",
        contact_result="right_party",
        outcome=_outcome("outbound", "pre_due", escalated={"reason": "hardship", "target": "200"}),
    )
    cc = _call(
        NOW,
        direction="inbound",
        campaign="inbound",
        contact_result="right_party",
        outcome=_outcome("inbound", "inbound"),
    )
    for body in (a, b, cc):
        assert api.post("/interactions", json=body).status_code == 200
    return {"A": a["call_id"], "B": b["call_id"], "C": cc["call_id"]}


def _ids(response: Any) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["call_id"] for item in response.json()["items"]]


def test_interactions_filters_combine_and_order_newest_first(api: TestClient) -> None:
    ids = _seed_calls(api)
    assert _ids(api.get("/interactions", params={"since": SINCE})) == [ids["C"], ids["B"], ids["A"]]
    assert _ids(api.get("/interactions", params={"since": SINCE, "campaign": "missed_payment"})) == [ids["A"]]
    assert _ids(api.get("/interactions", params={"since": SINCE, "direction": "inbound"})) == [ids["C"]]
    assert _ids(api.get("/interactions", params={"since": SINCE, "escalated": "true"})) == [ids["B"]]
    assert _ids(api.get("/interactions", params={"since": SINCE, "escalated": "false"})) == [
        ids["C"],
        ids["A"],
    ]
    both = {"since": SINCE, "campaign": "pre_due", "contact_result": "no_answer"}
    assert _ids(api.get("/interactions", params=both)) == []
    until = {"since": SINCE, "until": (NOW - dt.timedelta(minutes=45)).isoformat()}
    assert _ids(api.get("/interactions", params=until)) == [ids["A"]]


def test_interactions_pagination_and_limit_cap(api: TestClient) -> None:
    ids = _seed_calls(api)
    page = api.get("/interactions", params={"since": SINCE, "limit": 1, "offset": 1}).json()
    assert [i["call_id"] for i in page["items"]] == [ids["B"]]
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)
    capped = api.get("/interactions", params={"limit": 500}).json()
    assert capped["limit"] == 200
    assert api.get("/interactions").json()["limit"] == 50
    assert api.get("/interactions", params={"limit": 0}).status_code == 422


def test_call_detail_aggregates_everything_for_the_call(api: TestClient) -> None:
    ids = _seed_calls(api)
    call_id = ids["C"]
    promise = {"call_id": call_id, "loan_id": 9002, "promised_date": "2026-11-12", "amount": "200.72"}
    assert api.post("/promises", json=promise).status_code == 201
    task = {
        "call_id": call_id,
        "customer_id": 9002,
        "type": "callback",
        "reason": "customer asked",
        "due_at": (NOW + dt.timedelta(days=1)).isoformat(),
    }
    assert api.post("/tasks", json=task).status_code == 201
    for tool, ts in (("get_loan_status", NOW + dt.timedelta(seconds=9)), ("verify_identity", NOW)):
        audit = {
            "ts": ts.isoformat(),
            "call_id": call_id,
            "tool": tool,
            "args_hash": "0" * 64,
            "customer_id": 9002,
            "decision": "allow",
            "latency_ms": 5,
        }
        assert api.post("/audit", json=audit).status_code == 204
    response = api.get(f"/interactions/{call_id}")
    assert response.status_code == 200, response.text
    detail = c.CallDetail.model_validate(response.json())
    assert detail.interaction.call_id == call_id
    assert detail.customer_timezone == api.get("/customers/9002").json()["timezone"]
    assert [str(p.amount) for p in detail.promises] == ["200.72"]
    assert [t.reason for t in detail.tasks] == ["customer asked"]
    assert [a.tool for a in detail.audit] == ["verify_identity", "get_loan_status"]  # time order
    assert [o.channel for o in detail.outbox] == ["sms"]
    assert detail.interaction.versions is not None


def test_call_detail_unknown_is_404(api: TestClient) -> None:
    assert api.get(f"/interactions/{uuid.uuid4()}").status_code == 404
    assert api.get("/interactions/not-a-uuid").status_code == 404


def test_tasks_ordered_by_due_and_patchable(api: TestClient) -> None:
    created = []
    for hours, kind in ((5, "callback"), (1, "escalation"), (3, "callback")):
        body = {
            "customer_id": 9003,
            "type": kind,
            "reason": f"r{hours}",
            "due_at": (NOW + dt.timedelta(hours=hours)).isoformat(),
        }
        created.append(api.post("/tasks", json=body).json()["id"])
    open_tasks = api.get("/tasks", params={"status": "open", "customer_id": 9003}).json()
    assert [t["reason"] for t in open_tasks["items"]] == ["r1", "r3", "r5"]
    callbacks = api.get("/tasks", params={"type": "callback", "customer_id": 9003}).json()
    assert [t["reason"] for t in callbacks["items"]] == ["r3", "r5"]

    done = api.patch(f"/tasks/{created[1]}", json={"status": "done", "owner": "officer-1"})
    assert done.status_code == 200, done.text
    assert (done.json()["status"], done.json()["owner"]) == ("done", "officer-1")
    still_open = api.get("/tasks", params={"status": "open", "customer_id": 9003}).json()
    assert [t["reason"] for t in still_open["items"]] == ["r3", "r5"]
    assert api.patch(f"/tasks/{created[0]}", json={}).status_code == 422
    assert api.patch("/tasks/999999", json={"status": "done"}).status_code == 404


def test_promises_audit_and_outbox_lists(api: TestClient) -> None:
    bianca = api.get("/promises", params={"status": "open", "loan_id": 9012}).json()
    assert bianca["total"] == 1 and bianca["items"][0]["loan_id"] == 9012
    ids = _seed_calls(api)
    audit = {
        "ts": NOW.isoformat(),
        "call_id": ids["A"],
        "tool": "verify_identity",
        "args_hash": "1" * 64,
        "decision": "deny",
        "latency_ms": 3,
    }
    api.post("/audit", json=audit)
    rows = api.get("/audit", params={"call_id": ids["A"]}).json()
    assert [(r["tool"], r["decision"]) for r in rows["items"]] == [("verify_identity", "deny")]
    assert api.get("/audit", params={"decision": "deny", "call_id": ids["B"]}).json()["total"] == 0
    pending = api.get("/outbox", params={"status": "pending"}).json()
    assert {m["call_id"] for m in pending["items"]} >= {ids["B"], ids["C"]}


def test_ops_routes_need_the_service_token(api: TestClient) -> None:
    for path in ("/interactions", "/tasks", "/promises", "/audit", "/outbox"):
        assert api.get(path, headers={"X-Service-Token": "wrong"}).status_code == 401
