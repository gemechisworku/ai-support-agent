"""T-BANK-02 · BANK-R2: the synthetic population is deterministic, realistic in size, and synthetic."""

import datetime as dt
import re
import time
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from supportagent.bank import ledger
from supportagent.bank.seed import PRODUCTS, generate
from supportagent.bank.seed.__main__ import main as seed_cli
from supportagent.bank.seed.writer import checksum, row_counts, write
from supportagent.common import db
from supportagent.common.settings import Settings

AS_OF = dt.date(2026, 11, 9)
TARGETS = {"customers": 200, "accounts": 260, "loans": 320, "installments": 9_000, "transactions": 12_000}


def _within(actual: int, target: int) -> bool:
    return abs(actual - target) <= target * 0.05


@pytest.fixture(scope="module")
def data() -> object:
    return generate(42, AS_OF)


def test_same_seed_same_rows_other_seed_differs() -> None:
    assert generate(42, AS_OF) == generate(42, AS_OF)
    assert generate(43, AS_OF).customers != generate(42, AS_OF).customers


@pytest.mark.parametrize("seed", [42, 1, 2026])
def test_counts_near_architecture_targets(seed: int) -> None:
    data = generate(seed, AS_OF)
    for table, target in TARGETS.items():
        assert _within(len(getattr(data, table)), target), (table, len(getattr(data, table)))
    assert len(data.loan_products) == 5


def test_every_bucket_is_present(data: object) -> None:
    buckets = {loan["bucket"] for loan in data.loans}  # type: ignore[attr-defined]
    assert buckets == {"current", "1-30", "31-60", "61-90", "90+"}


def test_only_reserved_phones_and_example_emails(data: object) -> None:
    for c in data.customers:  # type: ignore[attr-defined]
        assert re.fullmatch(r"\+1555\d{7}", c["phone"]), c["phone"]
        assert c["email"].endswith("@example.com")
    phones = [c["phone"] for c in data.customers]  # type: ignore[attr-defined]
    assert len(set(phones)) == len(phones)


def test_cached_dpd_and_bucket_match_the_ledger(data: object) -> None:
    by_loan: dict[int, list[ledger.Line]] = {}
    for i in data.installments:  # type: ignore[attr-defined]
        by_loan.setdefault(i["loan_id"], []).append(
            ledger.Line(i["due_date"], i["principal_due"], i["interest_due"], i["fee_due"], i["paid_amount"])
        )
    for loan in data.loans:  # type: ignore[attr-defined]
        dpd = ledger.days_past_due(by_loan[loan["id"]], AS_OF)
        assert (loan["days_past_due"], loan["bucket"]) == (dpd, ledger.bucket(dpd))


def test_fees_only_after_grace(data: object) -> None:
    product_of = {loan["id"]: PRODUCTS[loan["product_code"]] for loan in data.loans}  # type: ignore[attr-defined]
    for i in data.installments:  # type: ignore[attr-defined]
        grace = product_of[i["loan_id"]].grace_days
        if i["fee_due"] > 0:
            assert ledger.fee_applies(i["due_date"], AS_OF, grace)


def test_products_are_the_five_policy_products() -> None:
    assert set(PRODUCTS) == {"PERSONAL", "AUTO", "MORTGAGE", "SME_WC", "SALARY_ADV"}
    assert PRODUCTS["AUTO"].late_fee_rule.cap == 50


# --------------------------------------------------------------------------- integration


@pytest.mark.integration
def test_seeding_twice_gives_identical_checksums(bank_db: Session) -> None:
    started = time.monotonic()
    counts = write(bank_db, generate(42, AS_OF), reset_first=True)
    elapsed = time.monotonic() - started
    first = checksum(bank_db)
    write(bank_db, generate(42, AS_OF), reset_first=True)
    assert checksum(bank_db) == first
    assert elapsed < 10, f"seeding took {elapsed:.1f}s"
    db_counts = row_counts(bank_db)
    for table, n in counts.items():
        assert db_counts[table] == n
    write(bank_db, generate(43, AS_OF), reset_first=True)
    assert checksum(bank_db) != first


@pytest.fixture
def empty_bank(bank_env: Settings) -> Iterator[Settings]:
    def truncate() -> None:
        with db.session_scope(db.bank_engine()) as s:
            s.execute(text("TRUNCATE customers, loan_products RESTART IDENTITY CASCADE"))

    truncate()
    yield bank_env
    truncate()


@pytest.mark.integration
def test_cli_seeds_and_refuses_to_overwrite_without_reset(
    empty_bank: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert seed_cli(["--seed", "42", "--as-of", "2026-11-09"]) == 0
    assert "checksum" in capsys.readouterr().out
    assert seed_cli(["--seed", "42", "--as-of", "2026-11-09"]) == 1
    assert "--reset" in capsys.readouterr().err
    assert seed_cli(["--seed", "42", "--as-of", "2026-11-09", "--reset"]) == 0
