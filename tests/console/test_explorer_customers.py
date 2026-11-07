"""T-CON-06 · CON-R8/R7: the customer and loan explorer, in Streamlit AppTest against the fake bank API."""

from collections.abc import Callable
from typing import Any

import pytest

from tests.console.conftest import FakeApi

pytest.importorskip("streamlit")


def _all_text(at: Any) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [t.value for t in at.title]
    for df in at.dataframe:
        parts.append(df.value.to_string())
    return "\n".join(str(p) for p in parts)


def test_search_lists_frank_with_masked_phone(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("customers.py")
    at.text_input(key="q").input("Frank").run()
    assert not at.exception
    table = at.dataframe[0].value
    assert list(table["Customer id"]) == [9002]
    assert table["Phone"].iloc[0] == "•••• 0302"
    assert "1 customers" in _all_text(at)


def test_search_by_loan_last4_and_bucket_filter(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("customers.py")
    at.text_input(key="q").input("4417").run()
    assert list(at.dataframe[0].value["Customer id"]) == [9002]
    at.text_input(key="q").input("").run()
    at.selectbox(key="bucket").select("1-30").run()
    sent = fake_api.requests[-1]
    assert sent.url.params["bucket"] == "1-30"


def test_frank_detail_matches_the_bank(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("customers.py", query={"customer": "9002"})
    assert not at.exception
    text = _all_text(at)
    assert "•••• 4417 · Personal Loan · 5 DPD · $200.72 overdue since 4 November 2026" in text
    tabs = [t.label for t in at.tabs]
    assert tabs == ["Profile", "Loans", "Schedule", "Transactions", "History"]
    loans = at.dataframe[1].value
    assert loans["Days past due"].iloc[0] == 5
    assert loans["Overdue"].iloc[0] == "$200.72"
    assert loans["Oldest unpaid due"].iloc[0] == "2026-11-04"
    schedule = at.dataframe[2].value
    assert len(schedule) > 1
    assert "missed" in set(schedule["Status"])
    assert len(at.dataframe[3].value) >= 1  # transactions


def test_detail_never_shows_full_numbers_or_dob(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("customers.py", query={"customer": "9002"})
    text = _all_text(at)
    frank = fake_api.bank.customers[9002]
    for loan in fake_api.bank.loans.values():
        assert loan.number not in text
    assert frank.phone not in text
    assert frank.dob.isoformat() not in text


def test_paid_already_priya_shows_pending_payment(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("customers.py", query={"customer": "9005"})
    transactions = at.dataframe[3].value
    assert "⏳ pending" in set(transactions["Status"])


def test_bianca_history_shows_open_promise(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("customers.py", query={"customer": "9012"})
    text = _all_text(at)
    assert "b1a2c3d4-0000-4000-8000-000000009012" in text  # her earlier call
    assert "open" in text


def test_api_error_is_shown_not_raised(page_test: Callable[..., Any]) -> None:
    from supportagent.console import clients as cl
    from tests.console.conftest import make_clients

    cl.set_clients(make_clients(FakeApi(fail={"/customers": 500})))
    try:
        at = page_test("customers.py")
        assert not at.exception
        assert any("HTTP 500" in e.value for e in at.error)
    finally:
        cl.set_clients(None)
