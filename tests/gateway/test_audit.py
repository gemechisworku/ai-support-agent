"""T-GW-04 · GW-R8: every request is audited before its result is returned, and fails closed."""

import hashlib
import logging

import pytest

from supportagent.gateway import session as session_mod
from supportagent.gateway.audit import args_hash, canonical_json
from supportagent.gateway.tools import UNAUTHENTICATED, Gateway, run_tool
from tests.gateway.conftest import CONFIG, open_session
from tests.gateway.fakes import FRANK, FakeBank
from tests.gateway.test_pipeline import REG


def test_args_hash_is_salted_canonical_sha256() -> None:
    args = {"b": 1, "a": "Frank"}
    expected = hashlib.sha256(("salt" + '{"a":"Frank","b":1}').encode()).hexdigest()
    assert canonical_json(args) == '{"a":"Frank","b":1}'
    assert args_hash("salt", args) == expected
    assert args_hash("salt", {"a": "Frank", "b": 1}) == expected  # order-independent
    assert args_hash("other", args) != expected
    assert args_hash("salt", None) == args_hash("salt", {})


async def test_one_row_per_request_including_denials(gateway: Gateway, bank: FakeBank) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    await run_tool(gateway, "verified", s.token, {}, REG)  # not_verified → deny
    session_mod.bind_customer(s, FRANK.customer_id)
    await run_tool(gateway, "verified", s.token, {"word": "x"}, REG)  # allow
    await run_tool(gateway, "verified", s.token, {"word": "bank"}, REG)  # upstream_error → error
    await run_tool(gateway, "any", None, {}, REG)  # forbidden → deny, unauthenticated
    rows = bank.audit
    assert [r.decision for r in rows] == ["deny", "allow", "error", "deny"]
    assert [r.call_id for r in rows] == [s.call_id] * 3 + [UNAUTHENTICATED]
    assert [r.customer_id for r in rows] == [None, FRANK.customer_id, FRANK.customer_id, None]
    assert all(r.tool in {"verified", "any"} and r.latency_ms >= 0 for r in rows)


async def test_raw_arguments_never_reach_the_audit_or_logs(
    gateway: Gateway, bank: FakeBank, caplog: pytest.LogCaptureFixture
) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    caplog.set_level(logging.DEBUG)
    await run_tool(gateway, "any", s.token, {"word": "Frank-1985-03-14"}, REG)
    dumped = bank.audit[0].model_dump_json()
    assert "Frank" not in dumped and "1985" not in dumped
    assert bank.audit[0].args_hash == args_hash(CONFIG.audit_salt, {"word": "Frank-1985-03-14"})
    assert "Frank-1985-03-14" not in caplog.text


async def test_fails_closed_when_the_audit_write_fails(gateway: Gateway, bank: FakeBank) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    bank.fail_audit = True
    result = await run_tool(gateway, "any", s.token, {"word": "secret-data"}, REG)
    assert not result.ok and result.data is None
    assert result.error is not None and result.error.code == "audit_unavailable"
