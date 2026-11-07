"""T-CON-01/T-CON-09 · CON-R7: masking, money, dates and the console API view builders."""

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


def test_loan_headline_for_frank() -> None:
    status = make_clients(FakeApi()).bank.loan_status(9002)
    assert (
        views.loan_headline(status)
        == "•••• 4417 · Personal Loan · 5 DPD · $200.72 overdue since 4 November 2026"
    )


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
