"""T-BANK-03 · BANK-R3: persona definitions are pure, unique and seed-independent (no database)."""

import datetime as dt
import re

from supportagent.bank.seed import PERSONAS, generate

AS_OF = dt.date(2026, 11, 9)


def test_twelve_personas_with_unique_ids_phones_extensions_and_loans() -> None:
    assert len(PERSONAS) == 12
    for field in ("customer_id", "phone", "extension", "loan_number", "loan_last4"):
        values = [getattr(p, field) for p in PERSONAS]
        assert len(set(values)) == 12, field
    assert [p.customer_id for p in PERSONAS] == list(range(9001, 9013))


def test_phones_are_e164_in_the_persona_block_and_match_extensions() -> None:
    for p in PERSONAS:
        assert re.fullmatch(r"\+155501003\d\d", p.phone)
        assert p.phone.endswith(p.extension)
        assert re.fullmatch(r"\d{10}", p.loan_number)


def test_each_persona_has_exactly_one_situation() -> None:
    for p in PERSONAS:
        assert (p.days_past_due > 0) != (p.next_due_in_days is not None), p.name


def _persona_rows(seed: int) -> dict[str, list[dict[str, object]]]:
    data = generate(seed, AS_OF)
    return {
        "customers": [r for r in data.customers if int(r["id"]) >= 9001],  # type: ignore[call-overload]
        "loans": [r for r in data.loans if int(r["id"]) >= 9001],  # type: ignore[call-overload]
        "installments": [r for r in data.installments if int(r["loan_id"]) >= 9001],  # type: ignore[call-overload]
        "transactions": [r for r in data.transactions if int(r["id"]) >= 9_000_000],  # type: ignore[call-overload]
        "promises": data.promises_to_pay,
    }


def test_persona_rows_do_not_depend_on_the_population_seed() -> None:
    assert _persona_rows(1) == _persona_rows(42)


def test_population_ids_and_phones_never_collide_with_personas() -> None:
    data = generate(42, AS_OF)
    ids = [r["id"] for r in data.customers]
    phones = [r["phone"] for r in data.customers]
    assert len(set(ids)) == len(ids) == 200
    assert len(set(phones)) == len(phones)
    assert len({r["id"] for r in data.transactions}) == len(data.transactions)
