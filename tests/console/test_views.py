"""T-CON-01 · CON-R7: pure view helpers (masking, money, dates, rows)."""

import datetime as dt
from decimal import Decimal

import pytest

from supportagent.console import views
from tests.console.conftest import FakeApi, make_clients


def test_mask_number() -> None:
    assert views.mask_number("4401228733101234") == "•••• 1234"


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("+15550100302", "•••• 0302"),
        ("552026-4417", "•••• 4417"),
        (4417, "•••• 4417"),
        (None, "—"),
        ("", "—"),
    ],
)
def test_mask_number_variants(raw: object, masked: str) -> None:
    assert views.mask_number(raw) == masked


def test_money_and_dates() -> None:
    assert views.money(Decimal("3813.79")) == "$3,813.79"
    assert views.money(None) == "—"
    assert views.human_date(dt.date(2026, 11, 4)) == "4 November 2026"
    assert views.date(dt.datetime(2026, 11, 9, 14, tzinfo=dt.UTC)) == "2026-11-09 14:00 UTC"


def test_loan_headline_for_frank() -> None:
    status = make_clients(FakeApi()).bank.loan_status(9002)
    assert (
        views.loan_headline(status)
        == "•••• 4417 · Personal Loan · 5 DPD · $200.72 overdue since 4 November 2026"
    )


def test_tables_never_show_full_numbers() -> None:
    api = FakeApi()
    bank = make_clients(api).bank
    page = bank.search_customers()
    rows = views.customer_rows(page) + views.loan_rows([bank.loan_status(9002)])
    flat = " ".join(str(v) for row in rows for v in row.values())
    for customer in api.bank.customers.values():
        assert customer.phone not in flat
    for loan in api.bank.loans.values():
        assert loan.number not in flat


def test_audit_rows_ordered_and_denials_marked() -> None:
    api = FakeApi()
    rows = views.audit_rows(list(reversed(api.audit)))
    assert [r["Tool"] for r in rows] == [a.tool for a in api.audit]
    assert rows[3]["Decision"] == "⛔ deny"


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("PMT-5520264417-001", "PMT-••••4417-001"),
        ("PMT-5520264417-PENDING", "PMT-••••4417-PENDING"),
        ("TRF 12345 / 4401228733101234", "TRF ••••2345 / ••••1234"),
        ("REF-001", "REF-001"),
        (None, views.DASH),
        ("", views.DASH),
    ],
)
def test_mask_reference(raw: str | None, masked: str) -> None:
    assert views.mask_reference(raw) == masked


def test_transaction_rows_mask_references() -> None:
    import datetime as dt
    from decimal import Decimal

    from supportagent.contracts import bank as b

    fields = b.Transaction.model_fields
    sample = {
        "id": 1,
        "account_id": 1,
        "loan_id": 9002,
        "ts": dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        "amount": Decimal("200.72"),
        "channel": "mobile",
        "status": "posted",
        "reference": "PMT-5520264417-001",
    }
    row = views.transaction_rows([b.Transaction(**{k: v for k, v in sample.items() if k in fields})])[0]
    assert "5520264417" not in str(row)
    assert row["Reference"] == "PMT-••••4417-001"


# ------------------------------------------------------------------- console API view builders (T-CON-09)


def _frank_bank() -> FakeApi:
    return FakeApi()


def test_view_builders_mask_and_keep_types() -> None:
    bank = make_clients(_frank_bank()).bank
    customer = bank.customer(9002)
    profile = views.customer_profile(customer)
    assert profile.phone == views.mask_number(customer.phone)
    assert "dob" not in views.CustomerProfile.model_fields

    status = bank.loan_status(9002)
    row = views.loan_row(status)
    assert row.number == "•••• 4417"
    assert row.overdue_amount == Decimal("200.72")
    assert isinstance(row.as_of, dt.date)


def test_transaction_view_masks_long_digit_runs() -> None:
    bank = make_clients(_frank_bank()).bank
    loan = bank.customer_loans(9002)[0]
    tx = bank.transactions(loan.repayment_account_id, loan.id, 1)[0]
    view = views.transaction_view(tx.model_copy(update={"reference": "PMT-4401228733101234-001"}))
    assert view.reference == "PMT-••••1234-001"


def test_views_are_allow_lists() -> None:
    """New contract fields never reach the UI by accident: no view has a DOB except the persona sheet."""
    with_dob = [
        name
        for name, model in vars(views).items()
        if isinstance(model, type) and issubclass(model, views._View) and "dob" in model.model_fields
    ]
    assert with_dob == ["PersonaView"]
