"""T-CON-07 · CON-R9: the policy explorer and search playground, against the fake gateway browse API."""

from collections.abc import Callable
from typing import Any

import pytest

from tests.console.conftest import FakeApi

pytest.importorskip("streamlit")


def test_document_list(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("policies.py")
    assert not at.exception
    docs = at.dataframe[0].value
    assert list(docs["Document"]) == ["fees-schedule", "late-fees-penalty-interest", "contact-calling-rules"]
    assert docs["Versions"].iloc[1] == "1.1 (from 2027-01-01), 1.0 (from 2026-01-01)"


def test_document_view_shows_chunks(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("policies.py")
    at.selectbox(key="doc").select("late-fees-penalty-interest").run()
    at.selectbox(key="version").select("1.0").run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert "#0 · Grace Periods > Grace periods by product · 5 tokens" in labels
    assert fake_api.requests[-1].url.params["version"] == "1.0"


def test_search_shows_citations_and_confidence(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("policies.py")
    at.text_input(key="query").input("late fee personal loan").run()
    assert not at.exception
    assert at.metric[0].value == "yes"
    results = at.dataframe[-1].value
    assert list(results["Citation"]) == ["fees-schedule@1.0#Schedule of Loan Fees"]
    assert not any("Low confidence" in w.value for w in at.warning)


def test_low_confidence_note(fake_api: FakeApi, page_test: Callable[..., Any]) -> None:
    at = page_test("policies.py")
    at.text_input(key="query").input("what is the weather tomorrow").run()
    assert at.metric[0].value == "no"
    assert any("offer a follow-up" in w.value for w in at.warning)


def test_document_error_keeps_search_usable(page_test: Callable[..., Any]) -> None:
    from supportagent.console import clients as cl
    from tests.console.conftest import make_clients

    cl.set_clients(make_clients(FakeApi(fail={"/service/kb/documents/": 404})))
    try:
        at = page_test("policies.py")
        assert not at.exception
        assert any("HTTP 404" in e.value for e in at.error)
        at.text_input(key="query").input("late fee personal loan").run()
        assert at.metric[0].value == "yes"
    finally:
        cl.set_clients(None)


def test_gateway_down_is_shown_not_raised(page_test: Callable[..., Any]) -> None:
    from supportagent.console import clients as cl
    from tests.console.conftest import make_clients

    cl.set_clients(make_clients(FakeApi(fail={"/service/kb": 503})))
    try:
        at = page_test("policies.py")
        assert not at.exception
        assert any("HTTP 503" in e.value for e in at.error)
    finally:
        cl.set_clients(None)
