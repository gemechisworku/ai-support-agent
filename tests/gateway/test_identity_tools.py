"""T-GW-05 · GW-R3, GW-R2: `lookup_by_phone` and `verify_identity` through the full pipeline."""

import datetime as dt
from typing import Any

from supportagent.gateway.tools import Gateway, run_tool
from tests.gateway.conftest import open_session
from tests.gateway.fakes import FRANK, HANA, MARCUS, FakeBank


def frank_claim(**over: Any) -> dict[str, Any]:
    claim = {"full_name": "Frank Fischer", "dob": FRANK.dob.isoformat(), "loan_last4": FRANK.loan_last4}
    claim.update(over)
    return claim


async def test_outbound_frank_verifies_and_binds(gateway: Gateway) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    r = await run_tool(gateway, "verify_identity", s.token, frank_claim(full_name="frank fisher"))
    assert r.ok and r.data is not None
    assert r.data.model_dump() == {
        "verified": True,
        "attempts_remaining": 2,
        "locked": False,
        "first_name": "Frank",
    }
    assert s.bound_customer_id == FRANK.customer_id


async def test_wrong_dob_twice_locks_and_blocks_everything_unverified(gateway: Gateway) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    wrong = frank_claim(dob="1985-03-15")
    first = await run_tool(gateway, "verify_identity", s.token, wrong)
    second = await run_tool(gateway, "verify_identity", s.token, wrong)
    assert first.data is not None and second.data is not None
    assert (first.data.attempts_remaining, first.data.locked) == (1, False)
    assert (second.data.attempts_remaining, second.data.locked, second.data.verified) == (0, True, False)
    third = await run_tool(gateway, "verify_identity", s.token, frank_claim())  # correct now, but too late
    assert third.error is not None and third.error.code == "locked"
    assert s.bound_customer_id is None


async def test_failure_never_names_the_factor(gateway: Gateway) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    outputs = []
    for claim in (frank_claim(dob="1999-01-01"), frank_claim(loan_last4="0000")):
        r = await run_tool(gateway, "verify_identity", s.token, claim)
        assert r.data is not None
        outputs.append(r.data.model_dump(exclude={"attempts_remaining", "locked"}))
    assert outputs == [{"verified": False, "first_name": None}] * 2


async def test_outbound_only_the_loans_customer_can_be_bound(gateway: Gateway) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    hana = {"full_name": HANA.name, "dob": HANA.dob.isoformat(), "loan_last4": HANA.loan_last4}
    r = await run_tool(gateway, "verify_identity", s.token, hana)
    assert r.data is not None and not r.data.verified
    assert s.bound_customer_id is None


async def test_spouse_answering_cannot_bind(gateway: Gateway) -> None:
    s = await open_session(gateway, loan_id=MARCUS.loan_id)
    spouse = {"full_name": "Linda Bell", "dob": "1970-02-02", "loan_last4": MARCUS.loan_last4}
    r = await run_tool(gateway, "verify_identity", s.token, spouse)
    assert r.data is not None and not r.data.verified and s.bound_customer_id is None


async def test_inbound_phone_candidate_binds(gateway: Gateway) -> None:
    s = await open_session(gateway, direction="inbound", campaign="inbound", caller_phone=FRANK.phone)
    r = await run_tool(gateway, "verify_identity", s.token, frank_claim())
    assert r.data is not None and r.data.verified and s.bound_customer_id == FRANK.customer_id


async def test_inbound_unknown_number_cannot_verify_yet(gateway: Gateway) -> None:
    """The bank has no identity-search endpoint (spec open question): the attempt counts as a failure."""
    s = await open_session(gateway, direction="inbound", campaign="inbound", caller_phone="+15559990000")
    r = await run_tool(gateway, "verify_identity", s.token, frank_claim())
    assert r.data is not None and not r.data.verified and r.data.attempts_remaining == 1


async def test_several_matches_count_as_a_failure(gateway: Gateway, bank: FakeBank) -> None:
    twin = bank.customers[FRANK.customer_id].model_copy(update={"id": 9999})
    bank.customers[9999] = twin
    bank.loans[9999] = bank.loans[FRANK.loan_id].model_copy(update={"id": 9999, "customer_id": 9999})
    s = await open_session(gateway, direction="inbound", campaign="inbound", caller_phone=FRANK.phone)
    assert sorted(s.candidate_ids) == [FRANK.customer_id, 9999]
    r = await run_tool(gateway, "verify_identity", s.token, frank_claim())
    assert r.data is not None and not r.data.verified and s.bound_customer_id is None


async def test_lookup_by_phone_reveals_a_name_only_on_outbound(gateway: Gateway) -> None:
    out = await open_session(gateway, loan_id=FRANK.loan_id)
    r = await run_tool(gateway, "lookup_by_phone", out.token, {})
    assert r.data is not None and r.data.model_dump() == {"match": True, "first_name": "Frank"}
    inbound = await open_session(gateway, direction="inbound", campaign="inbound", caller_phone=FRANK.phone)
    r = await run_tool(gateway, "lookup_by_phone", inbound.token, {})
    assert r.data is not None and r.data.model_dump() == {"match": True, "first_name": None}
    unknown = await open_session(
        gateway, direction="inbound", campaign="inbound", caller_phone="+15559990000"
    )
    r = await run_tool(gateway, "lookup_by_phone", unknown.token, {})
    assert r.data is not None and r.data.model_dump() == {"match": False, "first_name": None}


async def test_identity_tools_take_no_customer_id(gateway: Gateway) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    r = await run_tool(gateway, "lookup_by_phone", s.token, {"customer_id": 9003})
    assert r.error is not None and r.error.code == "invalid_input"
    r = await run_tool(gateway, "verify_identity", s.token, {**frank_claim(), "customer_id": 9003})
    assert r.error is not None and r.error.code == "invalid_input"
    assert s.failed_attempts == 0  # an invalid request is not an attempt


def test_dob_type() -> None:
    assert dt.date.fromisoformat(frank_claim()["dob"]) == FRANK.dob
