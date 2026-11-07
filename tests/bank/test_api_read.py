"""T-BANK-04 · BANK-R4/R5: read endpoints, auth, errors and persona values."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from supportagent.bank.api import create_app
from supportagent.bank.seed import PERSONA_BY_ID
from supportagent.contracts import bank as c

pytestmark = pytest.mark.integration

FRANK = PERSONA_BY_ID[9002]
PRIYA = PERSONA_BY_ID[9005]

ROUTES = [
    "/customers?phone=%2B15550100302",
    "/customers/9002",
    "/customers/9002/loans",
    "/customers/9002/interactions",
    "/loans/9002",
    "/loans/9002/installments",
    "/accounts/9002/transactions",
]


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("token", [None, "wrong"])
def test_every_route_requires_the_service_token(api: TestClient, path: str, token: str | None) -> None:
    headers = {"X-Service-Token": token} if token else {}
    with TestClient(api.app, headers=headers) as anonymous:  # type: ignore[arg-type]
        assert anonymous.get(path).status_code == 401


def test_healthz_needs_no_token() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/customers/1", "/loans/1", "/accounts/1/transactions"])
def test_routes_return_200_with_token(api: TestClient, path: str) -> None:
    assert api.get(path).status_code == 200


@pytest.mark.parametrize(
    "path", ["/customers/424242", "/customers/424242/loans", "/loans/424242", "/accounts/424242/transactions"]
)
def test_unknown_ids_are_404(api: TestClient, path: str) -> None:
    r = api.get(path)
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


@pytest.mark.parametrize("phone", ["5550100302", "+1-555", "abc"])
def test_invalid_phone_is_422(api: TestClient, phone: str) -> None:
    assert api.get("/customers", params={"phone": phone}).status_code == 422


def test_lookup_by_phone_finds_frank(api: TestClient) -> None:
    found = [
        c.Customer.model_validate(x) for x in api.get("/customers", params={"phone": FRANK.phone}).json()
    ]
    assert [(x.id, x.full_name, x.dob) for x in found] == [(9002, FRANK.name, FRANK.dob)]
    assert api.get("/customers", params={"phone": "+15559999999"}).json() == []


def test_frank_loan_status(api: TestClient) -> None:
    status = c.LoanStatus.model_validate(api.get(f"/loans/{FRANK.loan_id}").json())
    assert status.as_of.isoformat() == "2026-11-09"
    assert (status.days_past_due, status.bucket, status.number_last4) == (5, "1-30", FRANK.loan_last4)
    assert (
        status.oldest_unpaid_due_date is not None
        and status.oldest_unpaid_due_date.isoformat() == "2026-11-04"
    )
    assert status.overdue_amount > 0
    assert status.fees_accrued == Decimal("0.00")
    assert status.product_name == "Personal Loan"


def test_priya_pending_transaction_is_flagged_and_newest_first(api: TestClient) -> None:
    txs = [
        c.Transaction.model_validate(x) for x in api.get(f"/accounts/{PRIYA.customer_id}/transactions").json()
    ]
    assert len(txs) == 5
    assert txs[0].status == "pending"
    assert [t.ts for t in txs] == sorted((t.ts for t in txs), reverse=True)
    status = c.LoanStatus.model_validate(api.get(f"/loans/{PRIYA.loan_id}").json())
    assert txs[0].amount == status.overdue_amount


def test_transaction_limit_is_capped_at_50(api: TestClient) -> None:
    assert len(api.get("/accounts/1/transactions", params={"limit": 500}).json()) <= 50
    assert api.get("/accounts/1/transactions", params={"limit": 0}).status_code == 422


def test_installments_and_customer_loans(api: TestClient) -> None:
    rows = [c.Installment.model_validate(x) for x in api.get(f"/loans/{FRANK.loan_id}/installments").json()]
    assert [r.seq for r in rows] == list(range(1, 37))
    loans = api.get("/customers/9002/loans").json()
    assert [x["number"] for x in loans] == [FRANK.loan_number]


def test_bianca_history_has_the_call_and_open_promise(api: TestClient) -> None:
    history = c.InteractionHistory.model_validate(api.get("/customers/9012/interactions").json())
    assert len(history.interactions) == 1
    assert history.interactions[0].outcome is not None
    assert history.interactions[0].outcome.promise_to_pay is not None
    assert [p.status for p in history.open_promises] == ["open"]


def test_bank_as_of_setting_overrides_today(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from supportagent.common.settings import get_settings

    monkeypatch.setenv("BANK_AS_OF", "2026-11-12")
    get_settings.cache_clear()
    status = c.LoanStatus.model_validate(api.get(f"/loans/{FRANK.loan_id}").json())
    assert (status.as_of.isoformat(), status.days_past_due) == ("2026-11-12", 8)
