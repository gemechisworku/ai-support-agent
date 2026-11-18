"""T-BANK-08 · BANK-R10: identity match by DOB + loan last 4 for inbound callers from unknown numbers."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from supportagent.bank import queries

pytestmark = pytest.mark.integration


def test_frank_matches_exactly(api: TestClient) -> None:
    response = api.get("/customers/match", params={"dob": "1985-03-14", "loan_last4": "4417"})
    assert response.status_code == 200, response.text
    assert response.json() == [{"customer_id": 9002, "loan_id": 9002}]


@pytest.mark.parametrize(
    "params",
    [{"dob": "1985-03-15", "loan_last4": "4417"}, {"dob": "1985-03-14", "loan_last4": "4418"}],
)
def test_wrong_dob_or_last4_returns_nothing(api: TestClient, params: dict[str, str]) -> None:
    response = api.get("/customers/match", params=params)
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    "params",
    [
        {"dob": "1985-03-14"},
        {"loan_last4": "4417"},
        {"dob": "1985-03-14", "loan_last4": "441"},
        {"dob": "1985-03-14", "loan_last4": "44a7"},
        {"dob": "14/03/1985", "loan_last4": "4417"},
    ],
)
def test_both_parameters_are_required_and_validated(api: TestClient, params: dict[str, str]) -> None:
    assert api.get("/customers/match", params=params).status_code == 422


def test_match_needs_the_service_token(api: TestClient) -> None:
    params = {"dob": "1985-03-14", "loan_last4": "4417"}
    assert api.get("/customers/match", params=params, headers={"X-Service-Token": "x"}).status_code == 401


def test_customer_by_id_route_still_resolves(api: TestClient) -> None:
    assert api.get("/customers/9002").json()["id"] == 9002


def test_query_can_use_indexes_instead_of_scanning_loans(seeded_bank: object, bank_db: Session) -> None:
    """With sequential scans disabled the planner must still find a plan without `Seq Scan on loans`:
    customers by ix_customers_dob, then loans by ix_loans_customer_id."""
    stmt = queries.match_customers_stmt(dt.date(1985, 3, 14), "4417")
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    bank_db.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(row[0] for row in bank_db.execute(text(f"EXPLAIN {sql}")))
    assert "Seq Scan on loans" not in plan, plan
    assert "ix_customers_dob" in plan, plan
