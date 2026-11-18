"""T-GW-05 · GW-R3: name normalisation and matching.
T-GW-11 · GW-R3: inbound callers from an unregistered number (matched by DOB + loan last 4).
"""

from typing import Any

import pytest

from supportagent.gateway.identity import levenshtein, names_match, normalise_name
from supportagent.gateway.session import Session
from supportagent.gateway.tools import Gateway, run_tool
from tests.gateway.conftest import open_session
from tests.gateway.fakes import FRANK, FakeBank

UNKNOWN = "+15559990000"


def test_normalise_name() -> None:
    assert normalise_name("José  O'Neil") == ["jose", "oneil"]
    assert normalise_name("jose oneil") == ["jose", "oneil"]
    assert normalise_name("  FRANK   Fischer-Smith ") == ["frank", "fischersmith"]


def test_levenshtein() -> None:
    assert levenshtein("fischer", "fisher") == 1
    assert levenshtein("frank", "frank") == 0
    assert levenshtein("frank", "fronk") == 1
    assert levenshtein("frank", "frunky") == 2


@pytest.mark.parametrize(
    ("spoken", "ok"),
    [("Frank Fischer", True), ("frank fisher", True), ("Frank J. Fischer", True), ("Frenk Fischer", True),
     ("Franz Fisher", True), ("Fred Fischer", False), ("Frank Fishman", False), ("Frank", False),
     ("Fischer Frank", False)],
)  # fmt: skip
def test_names_match_within_one_edit_per_token(spoken: str, ok: bool) -> None:
    assert names_match(spoken, "Frank Fischer") is ok


# --------------------------------------------------------------------------- T-GW-11


def _claim(**over: Any) -> dict[str, Any]:
    claim = {"full_name": "Frank Fischer", "dob": FRANK.dob.isoformat(), "loan_last4": FRANK.loan_last4}
    claim.update(over)
    return claim


async def _unknown_caller(gateway: Gateway) -> Session:
    return await open_session(gateway, direction="inbound", campaign="inbound", caller_phone=UNKNOWN)


async def test_unknown_number_verifies_frank(gateway: Gateway) -> None:
    s = await _unknown_caller(gateway)
    r = await run_tool(gateway, "verify_identity", s.token, _claim())
    assert r.data is not None
    assert r.data.model_dump() == {
        "verified": True,
        "attempts_remaining": 2,
        "locked": False,
        "first_name": "Frank",
    }
    assert s.bound_customer_id == FRANK.customer_id


async def test_unknown_number_wrong_dob_counts_and_two_lock(gateway: Gateway) -> None:
    s = await _unknown_caller(gateway)
    wrong = _claim(dob="1985-03-15")
    first = await run_tool(gateway, "verify_identity", s.token, wrong)
    second = await run_tool(gateway, "verify_identity", s.token, wrong)
    assert first.data is not None and second.data is not None
    assert (first.data.verified, first.data.attempts_remaining, first.data.locked) == (False, 1, False)
    assert (second.data.verified, second.data.attempts_remaining, second.data.locked) == (False, 0, True)
    third = await run_tool(gateway, "verify_identity", s.token, _claim())  # correct now, but locked
    assert third.error is not None and third.error.code == "locked"
    assert s.bound_customer_id is None


async def test_unknown_number_match_with_another_name_does_not_verify(
    gateway: Gateway, bank: FakeBank
) -> None:
    s = await _unknown_caller(gateway)
    r = await run_tool(gateway, "verify_identity", s.token, _claim(full_name="Linda Bell"))
    assert r.data is not None
    # Same response shape as any failure: it never says which factor failed.
    assert r.data.model_dump(exclude={"attempts_remaining", "locked"}) == {
        "verified": False,
        "first_name": None,
    }
    assert s.bound_customer_id is None and s.failed_attempts == 1
    assert ("match_customers", (FRANK.dob, FRANK.loan_last4)) in bank.calls  # matched; the name check failed
