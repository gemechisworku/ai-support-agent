"""T-BANK-04 · BANK-R5: for every seeded loan, the API's outstanding balance equals the raw instalments."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_api_outstanding_matches_raw_instalments_for_all_loans(api: TestClient, bank_db: Session) -> None:
    expected = {
        loan_id: total
        for loan_id, total in bank_db.execute(
            text(
                "SELECT l.id, coalesce(sum(greatest("
                "i.principal_due + i.interest_due + i.fee_due - i.paid_amount, 0)), 0)"
                " FROM loans l LEFT JOIN installments i ON i.loan_id = l.id GROUP BY l.id"
            )
        )
    }
    assert len(expected) == 320
    for loan_id, total in expected.items():
        body = api.get(f"/loans/{loan_id}").json()
        assert Decimal(body["outstanding_balance"]) == Decimal(total).quantize(Decimal("0.01")), loan_id
