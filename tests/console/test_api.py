"""T-CON-09 · CON-R1/R7/R11: the console API over faked upstreams (no network).

The fake bank is the gateway tests' persona-seeded `FakeBank` with the bank's own ledger, so Frank's
figures equal what the real bank API returns for the 2026-11-09 seed.
"""

import json
import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from supportagent.console.api import create_app
from tests.console.conftest import CALL_ID, FakeApi, make_clients

API_TOKEN = "test-console-token"
AUTH = {"Authorization": f"Bearer {API_TOKEN}"}
FRANK = 9002


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def client(api: FakeApi) -> TestClient:
    return TestClient(create_app(make_clients(api), token=API_TOKEN))


def get(client: TestClient, path: str, **params: object) -> object:
    response = client.get(path, headers=AUTH, params=params)  # type: ignore[arg-type]
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- auth and errors


def test_healthz_needs_no_token(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic x"}])
def test_api_routes_require_the_token(client: TestClient, headers: dict[str, str]) -> None:
    assert client.get("/api/personas", headers=headers).status_code == 401


def test_upstream_failure_is_a_safe_502(api: FakeApi, client: TestClient) -> None:
    api.fail["/personas"] = 500
    response = client.get("/api/personas", headers=AUTH)
    assert response.status_code == 502
    assert response.json()["detail"].startswith("GET /personas failed: HTTP 500")


def test_unknown_customer_is_404(client: TestClient) -> None:
    response = client.get("/api/customers/424242", headers=AUTH)
    assert response.status_code == 404


def test_missing_token_setting_refuses_to_start(settings: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONSOLE_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="CONSOLE_API_TOKEN"):
        create_app()


def test_token_from_settings(settings: object, monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> None:
    from supportagent.common.settings import get_settings

    monkeypatch.setenv("CONSOLE_API_TOKEN", "from-env")
    get_settings.cache_clear()
    client = TestClient(create_app(make_clients(api)))
    assert client.get("/api/personas", headers={"Authorization": "Bearer from-env"}).status_code == 200


def test_limit_is_bounded(client: TestClient) -> None:
    assert client.get("/api/customers", headers=AUTH, params={"limit": 201}).status_code == 422


# --------------------------------------------------------------------------- explorer routes


def test_overview_counts(client: TestClient) -> None:
    body = get(client, "/api/overview")
    assert isinstance(body, dict)
    assert body["personas"] == 12
    assert body["kb_documents"] == 3
    assert body["customers"] > 0
    assert sum(body["loans_by_bucket"].values()) == body["loans"]


def test_customer_search_masks_phones(api: FakeApi, client: TestClient) -> None:
    body = get(client, "/api/customers", q="frank")
    assert isinstance(body, dict)
    assert body["total"] == 1
    (row,) = body["items"]
    assert row["customer_id"] == FRANK
    assert row["full_name"] == "Frank Fischer"
    assert re.fullmatch(r"•••• \d{4}", row["phone"])
    sent = api.requests[-1]
    assert sent.url.path == "/customers/search" and sent.url.params["q"] == "frank"


def test_frank_detail_matches_the_bank(client: TestClient) -> None:
    body = get(client, f"/api/customers/{FRANK}")
    assert isinstance(body, dict)
    assert body["profile"]["full_name"] == "Frank Fischer"
    assert "dob" not in body["profile"]
    (loan,) = body["loans"]
    assert loan["number"] == "•••• 4417"
    assert loan["days_past_due"] == 5
    assert Decimal(loan["overdue_amount"]) == Decimal("200.72")
    assert loan["oldest_unpaid_due_date"] == "2026-11-04"
    assert loan["headline"] == "•••• 4417 · Personal Loan · 5 DPD · $200.72 overdue since 4 November 2026"
    assert [i["seq"] for i in loan["installments"]] == sorted(i["seq"] for i in loan["installments"])
    assert loan["transactions"], "Frank has repayment transactions"
    assert set(body) == {"profile", "preferences", "loans", "interactions", "open_promises", "open_tasks"}


def test_full_numbers_never_leave_the_api(api: FakeApi, client: TestClient) -> None:
    full = "4401228733101234"
    api.bank.txs = [tx.model_copy(update={"reference": f"PMT-{full}-001"}) for tx in api.bank.txs]
    text = json.dumps(get(client, f"/api/customers/{FRANK}"), ensure_ascii=False)
    assert full not in text
    assert "PMT-••••1234-001" in text
    for phone_holder in (get(client, "/api/customers", q="frank"), get(client, f"/api/customers/{FRANK}")):
        assert api.bank.customers[FRANK].phone not in json.dumps(phone_holder)


def test_loans_filter_by_bucket(api: FakeApi, client: TestClient) -> None:
    body = get(client, "/api/loans", bucket="1-30", limit=200)
    assert isinstance(body, dict)
    assert body["items"] and all(row["bucket"] == "1-30" for row in body["items"])
    assert all(re.fullmatch(r"•••• \d{4}", row["number"]) for row in body["items"])
    assert api.requests[-1].url.params["bucket"] == "1-30"


def test_policies_pass_through(client: TestClient) -> None:
    docs = get(client, "/api/policies")
    assert isinstance(docs, list) and len(docs) == 3
    doc = get(client, "/api/policies/late-fees-penalty-interest", version="1.0")
    assert isinstance(doc, dict) and [c["ord"] for c in doc["chunks"]] == [0, 1]


@pytest.mark.parametrize(
    ("query", "confident"), [("late fee personal loan", True), ("capital of France", False)]
)
def test_policy_search(client: TestClient, query: str, confident: bool) -> None:
    response = client.post("/api/policies/search", headers=AUTH, json={"query": query})
    assert response.status_code == 200
    assert response.json()["confident"] is confident


def test_personas_are_the_only_view_with_dob(client: TestClient) -> None:
    personas = get(client, "/api/personas")
    assert isinstance(personas, list) and len(personas) == 12
    assert all(p["dob"] for p in personas)
    sam = next(p for p in personas if p["full_name"] == "Sam Carter")
    assert sam["loan_last4"] == "8842"


def test_audit_rows_in_order(api: FakeApi, client: TestClient) -> None:
    api.audit.reverse()  # upstream order must not matter
    rows = get(client, f"/api/audit/{CALL_ID}")
    assert isinstance(rows, list)
    assert [r["tool"] for r in rows] == [
        "verify_identity",
        "get_loan_status",
        "search_policy",
        "record_promise_to_pay",
        "record_call_outcome",
    ]
    assert rows[3]["decision"] == "deny"
    assert "args_hash" not in rows[0]


def test_every_documented_route_is_tested() -> None:
    paths = set(create_app(token="x").openapi()["paths"])
    assert paths == {
        "/healthz",
        "/api/overview",
        "/api/customers",
        "/api/customers/{customer_id}",
        "/api/loans",
        "/api/policies",
        "/api/policies/{doc_id}",
        "/api/policies/search",
        "/api/personas",
        "/api/audit/{call_id}",
    }
