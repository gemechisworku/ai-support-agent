"""T-GW-10 · GW-R11, GW-R8 (unit): the scheduler's service-scope API over FakeBank.

Candidate filters per campaign, the facts the compliance gate needs, the auth matrix (service ok;
worker/session token 403; none 401; the service token cannot call MCP tools), attempt validation and one
audit row per request.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from supportagent.common.clock import FrozenClock
from supportagent.contracts import bank as b
from supportagent.contracts.gateway import Candidate, ContactPrefs
from supportagent.gateway.app import create_app
from supportagent.gateway.tools import Gateway
from tests.gateway.conftest import CONFIG, open_session
from tests.gateway.fakes import FRANK, HANA, NOW, OLIVIA, SAM, FakeBank

SERVICE = {"Authorization": f"Bearer {CONFIG.service_token}"}
WORKER = {"Authorization": f"Bearer {CONFIG.worker_token}"}
CALL_ID = "6f1c2f3e-5b5e-4a53-9f43-0d6f1f5a0a11"


@pytest.fixture
def client(gateway: Gateway) -> TestClient:
    return TestClient(create_app(gateway))


def _candidates(client: TestClient, campaign: str) -> list[Candidate]:
    r = client.get(f"/service/candidates?campaign={campaign}", headers=SERVICE)
    assert r.status_code == 200, r.text
    return [Candidate.model_validate(x) for x in r.json()]


def _task(bank: FakeBank, *, customer_id: int, loan_id: int | None, due_at: dt.datetime) -> b.Task:
    task = b.Task(
        id=900 + len(bank.tasks),
        call_id=None,
        customer_id=customer_id,
        loan_id=loan_id,
        type="callback",
        reason="callback_requested",
        summary="",
        due_at=due_at,
        owner=None,
        status="open",
        created_at=NOW,
    )
    bank.tasks.append(task)
    return task


# --------------------------------------------------------------------------- campaigns


def test_missed_payment_lists_one_to_thirty_dpd_with_the_gate_facts(client: TestClient) -> None:
    rows = _candidates(client, "missed_payment")
    assert rows and all(1 <= r.days_past_due <= 30 for r in rows)
    assert all(r.campaign == "missed_payment" for r in rows)
    frank = next(r for r in rows if r.customer_id == FRANK.customer_id)
    assert frank.phone and frank.timezone and frank.amount_due is not None
    assert frank.contact.consent_voice_ai is True
    assert frank.attempts_last_7d >= 0
    assert "last_conversation_at" in frank.model_dump()


def test_a_customer_who_must_not_be_called_is_still_listed(client: TestClient, bank: FakeBank) -> None:
    """Filtering on contact rules is the scheduler's job (COMP), the gateway supplies the facts."""
    bank.prefs[SAM.customer_id] = bank.prefs[SAM.customer_id].model_copy(update={"cease_contact": True})
    sam = next(r for r in _candidates(client, "missed_payment") if r.customer_id == SAM.customer_id)
    assert sam.contact.cease_contact is True


def test_pre_due_lists_an_instalment_due_within_three_days(client: TestClient, bank: FakeBank) -> None:
    rows = _candidates(client, "pre_due")
    assert OLIVIA.customer_id in {r.customer_id for r in rows}
    assert all(r.campaign == "pre_due" and r.due_date is not None for r in rows)
    assert all(r.due_date <= NOW.date() + dt.timedelta(days=3) for r in rows if r.due_date)
    assert ("eligible_loans", (None, 3)) in bank.calls


def test_missed_payment_asks_the_bank_for_the_one_to_thirty_bucket(
    client: TestClient, bank: FakeBank
) -> None:
    _candidates(client, "missed_payment")
    assert ("eligible_loans", ("1-30", None)) in bank.calls


def test_callback_lists_due_callback_tasks_only(client: TestClient, bank: FakeBank) -> None:
    loan = next(ln for ln in bank.loans.values() if ln.customer_id == FRANK.customer_id)
    due = _task(bank, customer_id=FRANK.customer_id, loan_id=loan.id, due_at=NOW - dt.timedelta(hours=1))
    _task(bank, customer_id=HANA.customer_id, loan_id=None, due_at=NOW + dt.timedelta(days=1))  # not due
    rows = _candidates(client, "callback")
    assert [r.task_id for r in rows] == [due.id]
    assert rows[0].customer_id == FRANK.customer_id and rows[0].loan_id == loan.id
    assert rows[0].campaign == "callback"
    held = bank.prefs[FRANK.customer_id]
    assert rows[0].contact.model_dump() == {k: getattr(held, k) for k in ContactPrefs.model_fields}


def test_callback_resolves_a_loan_when_the_task_has_none(client: TestClient, bank: FakeBank) -> None:
    _task(bank, customer_id=HANA.customer_id, loan_id=None, due_at=NOW - dt.timedelta(minutes=5))
    rows = _candidates(client, "callback")
    assert len(rows) == 1 and bank.loans[rows[0].loan_id].customer_id == HANA.customer_id


def test_callback_includes_a_broken_promise(client: TestClient, bank: FakeBank) -> None:
    loan = next(ln for ln in bank.loans.values() if ln.customer_id == FRANK.customer_id)
    bank.promises.append(
        b.Promise(
            id=777,
            call_id=None,
            loan_id=loan.id,
            promised_date=NOW.date() - dt.timedelta(days=2),
            amount=bank.status_of(loan.id).overdue_amount,
            status="broken",
            created_at=NOW - dt.timedelta(days=5),
        )
    )
    rows = _candidates(client, "callback")
    broken = [r for r in rows if r.task_id is None]
    assert len(broken) == 1 and broken[0].loan_id == loan.id
    assert broken[0].due_date == NOW.date() - dt.timedelta(days=2)


def test_callback_counts_attempts_and_conversations_from_the_loans_interactions(
    client: TestClient, bank: FakeBank
) -> None:
    loan = next(ln for ln in bank.loans.values() if ln.customer_id == FRANK.customer_id)
    _task(bank, customer_id=FRANK.customer_id, loan_id=loan.id, due_at=NOW - dt.timedelta(hours=1))
    expected = next(r for r in bank_eligible(bank) if r.loan_id == loan.id)
    row = _candidates(client, "callback")[0]
    assert row.attempts_last_7d == expected.attempts_last_7d
    assert row.last_conversation_at == expected.last_conversation_at


def bank_eligible(bank: FakeBank) -> list[b.EligibleLoan]:
    import asyncio

    return [
        *asyncio.run(bank.eligible_loans(bucket="1-30")),
        *asyncio.run(bank.eligible_loans(bucket="31-60")),
        *asyncio.run(bank.eligible_loans(bucket="61-90")),
        *asyncio.run(bank.eligible_loans(bucket="90+")),
        *asyncio.run(bank.eligible_loans(due_within=31)),
    ]


def test_an_unknown_campaign_is_rejected(client: TestClient) -> None:
    assert client.get("/service/candidates?campaign=nope", headers=SERVICE).status_code == 422
    assert client.get("/service/candidates", headers=SERVICE).status_code == 422


# --------------------------------------------------------------------------- contact preferences


def test_contact_preferences_are_returned_as_the_bank_holds_them(client: TestClient, bank: FakeBank) -> None:
    bank.prefs[SAM.customer_id] = bank.prefs[SAM.customer_id].model_copy(update={"cease_contact": True})
    r = client.get(f"/service/contact-preferences/{SAM.customer_id}", headers=SERVICE)
    assert r.status_code == 200
    prefs = ContactPrefs.model_validate(r.json())
    assert prefs.cease_contact is True
    assert set(r.json()) == {"consent_voice_ai", "dnc", "cease_contact", "dispute_open", "preferred_window"}


def test_contact_preferences_of_an_unknown_customer_are_404(client: TestClient) -> None:
    assert client.get("/service/contact-preferences/424242", headers=SERVICE).status_code == 404


# --------------------------------------------------------------------------- attempts


def _attempt(bank: FakeBank, **kw: object) -> dict[str, object]:
    loan = next(ln for ln in bank.loans.values() if ln.customer_id == FRANK.customer_id)
    body: dict[str, object] = {
        "call_id": CALL_ID,
        "customer_id": FRANK.customer_id,
        "loan_id": loan.id,
        "campaign": "missed_payment",
        "contact_result": "no_answer",
        "started_at": NOW.isoformat(),
    }
    body.update(kw)
    return body


def test_an_attempt_creates_an_outbound_interaction_row(client: TestClient, bank: FakeBank) -> None:
    r = client.post("/service/attempts", json=_attempt(bank), headers=SERVICE)
    assert r.status_code == 200, r.text
    assert r.json() == {"interaction_id": CALL_ID}
    row = bank.interactions[CALL_ID]
    assert row.direction == "outbound" and row.campaign == "missed_payment"
    assert row.contact_result == "no_answer" and row.customer_id == FRANK.customer_id
    assert row.outcome is None


@pytest.mark.parametrize(
    "result", ["right_party", "third_party", "voicemail", "busy", "no_answer", "wrong_number"]
)
def test_every_prd_contact_result_is_accepted(client: TestClient, bank: FakeBank, result: str) -> None:
    r = client.post("/service/attempts", json=_attempt(bank, contact_result=result), headers=SERVICE)
    assert r.status_code == 200, r.text


def test_a_contact_result_outside_the_enum_is_rejected(client: TestClient, bank: FakeBank) -> None:
    r = client.post("/service/attempts", json=_attempt(bank, contact_result="maybe"), headers=SERVICE)
    assert r.status_code == 422
    assert bank.writes() == []


def test_a_naive_start_time_is_rejected(client: TestClient, bank: FakeBank) -> None:
    r = client.post(
        "/service/attempts", json=_attempt(bank, started_at="2026-11-09T14:00:00"), headers=SERVICE
    )
    assert r.status_code == 422


def test_an_attempt_for_an_unknown_loan_is_404(client: TestClient, bank: FakeBank) -> None:
    r = client.post("/service/attempts", json=_attempt(bank, loan_id=424242), headers=SERVICE)
    assert r.status_code == 404
    assert bank.writes() == []


def test_an_attempt_whose_loan_belongs_to_someone_else_is_422(client: TestClient, bank: FakeBank) -> None:
    r = client.post("/service/attempts", json=_attempt(bank, customer_id=OLIVIA.customer_id), headers=SERVICE)
    assert r.status_code == 422
    assert bank.writes() == []


# --------------------------------------------------------------------------- auth and audit

ROUTES = [
    ("GET", "/service/candidates?campaign=pre_due"),
    ("GET", f"/service/contact-preferences/{SAM.customer_id}"),
    ("POST", "/service/attempts"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_no_credential_is_401(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path, json={}).status_code == 401


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_the_worker_token_is_403(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path, headers=WORKER, json={}).status_code == 403


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_a_session_token_is_403(
    gateway: Gateway, client: TestClient, bank: FakeBank, method: str, path: str
) -> None:
    session = await open_session(gateway)
    r = client.request(method, path, headers={"Authorization": f"Bearer {session.token}"}, json={})
    assert r.status_code == 403
    assert bank.writes() == []


def test_the_service_token_cannot_call_mcp_tools(gateway: Gateway, bank: FakeBank) -> None:
    """The service token is not a session token: a tool call with it is denied and reads no account data."""
    with TestClient(create_app(gateway)) as client:  # the context runs the MCP session manager
        r = client.post(
            "/mcp",
            headers={**SERVICE, "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_loan_status", "arguments": {}},
            },
        )
    body = r.text
    assert '"ok":false' in body.replace(" ", "") or r.status_code in (401, 403), body
    assert "overdue" not in body and "outstanding" not in body
    assert not [n for n, _ in bank.calls if n in {"loan_status", "customer_loans", "transactions"}]


def test_every_request_writes_one_audit_row_with_the_service_call_id(
    client: TestClient, bank: FakeBank, frozen_clock: FrozenClock
) -> None:
    client.get("/service/candidates?campaign=pre_due", headers=SERVICE)
    client.get(f"/service/contact-preferences/{SAM.customer_id}", headers=SERVICE)
    client.post("/service/attempts", json=_attempt(bank), headers=SERVICE)
    client.get("/service/candidates?campaign=pre_due", headers=WORKER)  # denied, and audited
    assert [a.call_id for a in bank.audit] == ["service"] * 4
    assert [a.tool for a in bank.audit] == [
        "service.candidates",
        "service.contact_preferences",
        "service.record_attempt",
        "service.candidates",
    ]
    assert [a.decision for a in bank.audit] == ["allow", "allow", "allow", "deny"]


def test_the_audit_row_stores_a_hash_of_the_arguments_not_the_arguments(
    client: TestClient, bank: FakeBank
) -> None:
    client.post("/service/attempts", json=_attempt(bank), headers=SERVICE)
    dumped = bank.audit[0].model_dump_json()
    assert CALL_ID not in dumped and FRANK.phone not in dumped


def test_a_failed_audit_write_fails_the_request_closed(client: TestClient, bank: FakeBank) -> None:
    bank.fail_audit = True
    r = client.get("/service/candidates?campaign=pre_due", headers=SERVICE)
    assert r.status_code == 503
