"""Deterministic synthetic data for the mock bank (BANK-R2, BANK-R3).

    uv run python -m supportagent.bank.seed --seed 42 --as-of 2026-11-09 --reset

The random population (sized so that, with the 12 personas, totals hit the architecture targets:
200 customers, 260 accounts, 320 loans) varies with `seed`; persona rows never do.
`PRODUCTS` and `PERSONAS` are importable without a database.
"""

import datetime as dt

from supportagent.bank.seed.builder import SeedData
from supportagent.bank.seed.personas import PERSONA_BY_ID, PERSONAS, Persona, generate_personas
from supportagent.bank.seed.population import generate_population
from supportagent.bank.seed.products import PRODUCTS

__all__ = ["PERSONAS", "PERSONA_BY_ID", "PRODUCTS", "Persona", "SeedData", "generate"]

N_PERSONAS = len(PERSONAS)


def generate(seed: int, as_of: dt.date) -> SeedData:
    """All seed rows for `seed` and `as_of`: population, then personas (ids 9001-9012)."""
    data = generate_population(
        seed,
        as_of,
        n_customers=200 - N_PERSONAS,
        n_accounts=260 - N_PERSONAS,
        n_loans=320 - N_PERSONAS,
    )
    data.extend(generate_personas(as_of))
    return data
