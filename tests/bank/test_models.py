"""T-BANK-01 · BANK-R1: the bank schema migrates up and down and matches the models."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from supportagent.bank import models
from supportagent.common import db, migrate
from supportagent.common.db import BankBase
from supportagent.common.settings import Settings

EXPECTED_INDEXES = {
    "customers": {("phone",)},
    "loans": {("customer_id",)},
    "installments": {("due_date",)},
    "transactions": {("account_id", "ts")},
}


def test_metadata_has_all_twelve_tables() -> None:
    assert sorted(BankBase.metadata.tables) == sorted(models.TABLES)
    assert len(models.TABLES) == 12


def test_ledger_money_columns_are_numeric_12_2() -> None:
    for table, column in [
        ("installments", "principal_due"),
        ("loans", "principal"),
        ("transactions", "amount"),
    ]:
        col = BankBase.metadata.tables[table].c[column]
        assert (col.type.precision, col.type.scale) == (12, 2)  # type: ignore[attr-defined]


@pytest.mark.integration
def test_upgrade_creates_tables_and_indexes_then_downgrade_drops(bank_env: Settings) -> None:
    migrate.upgrade("bank")
    insp = inspect(db.bank_engine())
    assert set(models.TABLES) <= set(insp.get_table_names())
    for table, wanted in EXPECTED_INDEXES.items():
        have = {tuple(ix["column_names"]) for ix in insp.get_indexes(table)}
        assert wanted <= have, f"{table}: {have}"
    columns = {c["name"] for c in insp.get_columns("loans")}
    assert {"days_past_due", "bucket", "repayment_account_id"} <= columns

    migrate.downgrade("bank", "bank_0001")
    insp = inspect(db.bank_engine())
    assert not set(models.TABLES) & set(insp.get_table_names())
    migrate.upgrade("bank")  # leave bank_test at head for the other tests


@pytest.mark.integration
def test_migration_matches_models(bank_env: Settings) -> None:
    migrate.upgrade("bank")
    with db.bank_engine().connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), BankBase.metadata)
    assert diff == [], "models and migration disagree; regenerate or fix bank_0002"
