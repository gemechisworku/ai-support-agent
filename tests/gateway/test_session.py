"""T-GW-02 · GW-R2: per-call sessions, tokens, TTL, and the session API."""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from supportagent.common.clock import FrozenClock
from supportagent.gateway import session as session_mod
from supportagent.gateway.app import create_app
from supportagent.gateway.tools import Gateway
from tests.gateway.conftest import CONFIG, open_session
from tests.gateway.fakes import FRANK

ROOT = Path(__file__).resolve().parents[2]


async def test_outbound_session_expects_the_loans_customer(gateway: Gateway) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    assert s.expected_customer_id == FRANK.customer_id
    assert not s.verified and s.attempts_remaining == 2
    assert len(s.token) >= 40


async def test_inbound_session_collects_phone_candidates(gateway: Gateway) -> None:
    s = await open_session(gateway, direction="inbound", campaign="inbound", caller_phone=FRANK.phone)
    assert s.candidate_ids == [FRANK.customer_id]
    unknown = await open_session(
        gateway, direction="inbound", campaign="inbound", caller_phone="+15559999999"
    )
    assert unknown.candidate_ids == []


async def test_create_is_idempotent_per_call(gateway: Gateway) -> None:
    call_id = str(uuid.uuid4())
    a = await open_session(gateway, call_id=call_id, loan_id=FRANK.loan_id)
    b = await open_session(gateway, call_id=call_id, loan_id=FRANK.loan_id)
    assert a is b and a.token == b.token


async def test_resolve_rejects_missing_unknown_and_expired(
    gateway: Gateway, frozen_clock: FrozenClock
) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    assert gateway.store.resolve(None) is None
    assert gateway.store.resolve("nope") is None
    assert gateway.store.resolve(s.token) is s
    frozen_clock.advance(minutes=29)
    assert gateway.store.resolve(s.token) is s  # touched: idle timer restarts
    frozen_clock.advance(minutes=31)
    assert gateway.store.resolve(s.token) is None  # idle > 30 min


async def test_max_lifetime(gateway: Gateway, frozen_clock: FrozenClock) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    for _ in range(4):
        frozen_clock.advance(minutes=15)
        assert gateway.store.resolve(s.token) is s
    frozen_clock.advance(minutes=1)
    assert gateway.store.resolve(s.token) is None  # 61 min > 60 min max


async def test_close_and_binding_rules(gateway: Gateway) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    session_mod.bind_customer(s, FRANK.customer_id)
    with pytest.raises(PermissionError):
        session_mod.bind_customer(s, 9003)
    assert gateway.store.close(s.call_id) and s.closed
    assert not gateway.store.close("unknown")


def test_only_identity_module_binds_customers() -> None:
    callers = [
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "src" / "supportagent").rglob("*.py")
        if "bind_customer(" in p.read_text() and p.name != "session.py"
    ]
    assert callers in ([], ["src/supportagent/gateway/identity.py"])


def test_session_api_requires_the_worker_token(gateway: Gateway) -> None:
    client = TestClient(create_app(gateway))
    body = {
        "call_id": str(uuid.uuid4()),
        "direction": "outbound",
        "campaign": "missed_payment",
        "loan_id": 9002,
    }
    assert client.post("/sessions", json=body).status_code == 401
    assert client.post("/sessions", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.post("/sessions", json=body, headers={"Authorization": f"Bearer {CONFIG.worker_token}"})
    assert ok.status_code == 200
    token = ok.json()["session_token"]
    assert gateway.store.resolve(token) is not None
    again = client.post("/sessions", json=body, headers={"Authorization": f"Bearer {CONFIG.worker_token}"})
    assert again.json()["session_token"] == token
    assert client.delete(f"/sessions/{body['call_id']}").status_code == 401
    closed = client.delete(
        f"/sessions/{body['call_id']}", headers={"Authorization": f"Bearer {CONFIG.worker_token}"}
    )
    assert closed.status_code == 204
    assert client.get("/healthz").json() == {"status": "ok"}


def test_session_api_rejects_a_customer_id(gateway: Gateway) -> None:
    client = TestClient(create_app(gateway))
    body = {"call_id": str(uuid.uuid4()), "direction": "inbound", "customer_id": 9002}
    resp = client.post("/sessions", json=body, headers={"Authorization": f"Bearer {CONFIG.worker_token}"})
    assert resp.status_code == 422
