"""T-BANK-09 · BANK-R11: explorer endpoints (customer search, loan list, preferences, personas)."""

import pytest
from fastapi.testclient import TestClient

from supportagent.contracts import bank as c

pytestmark = pytest.mark.integration

FRANK = 9002


def _search(api: TestClient, **params: object) -> c.Page[c.CustomerSummary]:
    response = api.get("/customers/search", params=params)
    assert response.status_code == 200, response.text
    return c.Page[c.CustomerSummary].model_validate(response.json())


@pytest.mark.parametrize("q", ["frank", "FRANK", "+15550100302", "9002", "4417"])
def test_search_finds_frank_by_name_phone_id_or_loan_last4(api: TestClient, q: str) -> None:
    page = _search(api, q=q)
    frank = next(x for x in page.items if x.customer_id == FRANK)
    assert frank.phone == "+15550100302"
    assert frank.loan_count >= 1
    assert frank.worst_bucket == "1-30"  # 5 DPD at 2026-11-09


def test_search_without_query_pages_through_everyone(api: TestClient) -> None:
    first = _search(api, limit=10)
    assert first.total == 200
    assert [x.customer_id for x in first.items] == sorted(x.customer_id for x in first.items)
    second = _search(api, limit=10, offset=10)
    assert {x.customer_id for x in first.items}.isdisjoint({x.customer_id for x in second.items})
    assert _search(api, limit=1000).limit == 200
    assert api.get("/customers/search", params={"limit": 0}).status_code == 422


def test_search_filters_by_segment_and_bucket(api: TestClient) -> None:
    segment = api.get(f"/customers/{FRANK}").json()["segment"]
    by_segment = _search(api, segment=segment, limit=200)
    assert by_segment.total > 0
    assert all(x.segment == segment for x in by_segment.items)
    delinquent = _search(api, bucket="1-30", limit=200)
    assert all(x.worst_bucket == "1-30" for x in delinquent.items)
    assert FRANK in {x.customer_id for x in delinquent.items}
    assert FRANK not in {x.customer_id for x in _search(api, bucket="current", limit=200).items}


def test_loan_list_filters_and_matches_loan_status(api: TestClient) -> None:
    response = api.get("/loans", params={"bucket": "1-30", "product": "PERSONAL", "limit": 200})
    assert response.status_code == 200, response.text
    page = c.Page[c.LoanStatus].model_validate(response.json())
    assert page.total == len(page.items) > 0
    assert all(x.bucket == "1-30" and x.product_code == "PERSONAL" for x in page.items)
    frank = next(x for x in page.items if x.loan_id == FRANK)
    assert frank == c.LoanStatus.model_validate(api.get(f"/loans/{FRANK}").json())
    everything = api.get("/loans", params={"limit": 10}).json()
    assert everything["total"] == 320
    assert api.get("/loans", params={"bucket": "late"}).status_code == 422


def test_contact_preferences(api: TestClient) -> None:
    sam = api.get("/customers/9007/contact-preferences")
    assert sam.status_code == 200, sam.text
    prefs = c.ContactPreferences.model_validate(sam.json())
    assert prefs.customer_id == 9007
    assert api.get("/customers/999999/contact-preferences").status_code == 404


def test_personas_are_the_synthetic_test_identities(api: TestClient) -> None:
    response = api.get("/personas")
    assert response.status_code == 200, response.text
    cards = [c.PersonaCard.model_validate(x) for x in response.json()]
    assert [x.customer_id for x in cards] == list(range(9001, 9013))
    frank = next(x for x in cards if x.customer_id == FRANK)
    assert (str(frank.dob), frank.loan_last4, frank.extension) == ("1985-03-14", "4417", "302")
    assert frank.situation and frank.expected_outcome
    schema = api.get("/openapi.json").json()["components"]["schemas"]["PersonaCard"]
    assert "synthetic test identities" in schema["description"]


def test_existing_customer_routes_still_resolve(api: TestClient) -> None:
    assert [x["id"] for x in api.get("/customers", params={"phone": "+15550100302"}).json()] == [FRANK]
    assert api.get(f"/customers/{FRANK}").json()["id"] == FRANK


def test_explorer_routes_need_the_service_token(api: TestClient) -> None:
    bad = {"X-Service-Token": "wrong"}
    for path in ("/customers/search", "/loans", "/personas", f"/customers/{FRANK}/contact-preferences"):
        assert api.get(path, headers=bad).status_code == 401, path
