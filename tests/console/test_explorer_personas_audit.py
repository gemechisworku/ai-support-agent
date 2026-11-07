"""T-CON-08 · CON-R10: persona test sheet and call audit trail, against the fake bank API."""

from collections.abc import Callable
from typing import Any

import pytest

from tests.console.conftest import CALL_ID, FakeApi

pytest.importorskip("streamlit")


def test_persona_sheet_lists_12_test_identities(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("personas.py")
    assert not at.exception
    table = at.dataframe[0].value
    assert len(table) == 12
    frank = table[table["Customer id"] == 9002].iloc[0]
    assert frank["DOB (test identity)"] == "1985-03-14"
    assert frank["Loan last 4"] == "4417"
    assert frank["Extension"] == "302"
    assert any("test identities" in i.value for i in at.info)


def test_audit_trail_in_order_with_denials(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("audit.py")
    at.text_input(key="call_id").input(CALL_ID).run()
    assert not at.exception
    rows = at.dataframe[0].value
    assert list(rows["Tool"]) == [
        "verify_identity",
        "get_loan_status",
        "search_policy",
        "record_promise_to_pay",
        "record_call_outcome",
    ]
    assert rows["Decision"].iloc[3] == "⛔ deny"
    assert at.metric[0].value == "5"
    assert any("denied or failed" in e.value for e in at.error)


def test_audit_trail_via_query_param(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("audit.py", query={"call_id": CALL_ID})
    assert len(at.dataframe[0].value) == 5


def test_unknown_call_id(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("audit.py")
    at.text_input(key="call_id").input("00000000-0000-4000-8000-000000000000").run()
    assert any("No audit rows" in i.value for i in at.info)
