"""Money: `Decimal`, two decimal places, never float arithmetic (AGENTS.md conventions).

Accepts `Decimal`, `int`, numeric strings and floats that are exact at 2 dp (`245.5` → `245.50`);
rejects anything with more than 2 decimal places, negatives, and amounts ≥ 10^10 (`NUMERIC(12,2)`).
Values are normalised to exactly 2 dp and serialise to JSON as strings (`"245.50"`).
"""

from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, Field

CENT = Decimal("0.01")


def _two_dp(value: Decimal) -> Decimal:
    return value.quantize(CENT)


Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2), AfterValidator(_two_dp)]
"""A non-negative amount with 2 decimal places, matching SQL `NUMERIC(12,2)`."""
