"""Deterministic synthetic data for the mock bank (BANK-R2, BANK-R3).

    uv run python -m supportagent.bank.seed --seed 42 --as-of 2026-11-09 --reset

`PRODUCTS` is importable without a database (the KB corpus cross-check reads it).
"""

import datetime as dt

from supportagent.bank.seed.builder import SeedData
from supportagent.bank.seed.population import generate_population
from supportagent.bank.seed.products import PRODUCTS

__all__ = ["PRODUCTS", "SeedData", "generate"]


def generate(seed: int, as_of: dt.date) -> SeedData:
    """All seed rows for `seed` and `as_of`."""
    return generate_population(seed, as_of)
