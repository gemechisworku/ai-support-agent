"""Masking and response shaping (GW-R5, GW-R10).

Tool outputs are built field by field from explicit lists — bank DTOs are never passed through — and
numbers that identify a customer are cut to their last 4 digits. Text that came from documents is
sanitised before it reaches the model: control/zero-width characters and ANSI escapes are removed
and it is truncated, so it stays data rather than instructions.

`speakable_*` helpers render amounts and dates the way the agent should say them, from `Decimal`
values quantised half-up to 2 dp (never floats), so the spoken figure equals the bank figure.
"""

import datetime as dt
import json
import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel

MAX_TEXT = 2000
CENT = Decimal("0.01")

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_DIGIT_RUN = re.compile(r"\d{5,}")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def mask_number(value: str | None) -> str | None:
    """`5520264417` → `****4417`. Numbers of 4 digits or fewer are fully masked."""
    if value is None:
        return None
    digits = _digits(value)
    return "****" + digits[-4:] if len(digits) > 4 else "****"


def mask_phone(value: str | None) -> str | None:
    """`+15550100302` → `***-***-0302`."""
    if value is None:
        return None
    digits = _digits(value)
    return "***-***-" + digits[-4:] if len(digits) > 4 else "***-***-****"


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def speakable_money(value: Decimal) -> str:
    """`Decimal("1245.1")` → `$1,245.10`."""
    return f"${money(value):,.2f}"


def speakable_date(value: dt.date) -> str:
    """`date(2026, 11, 4)` → `4 November 2026`."""
    return f"{value.day} {value:%B %Y}"


def sanitize_text(text: str, limit: int = MAX_TEXT) -> str:
    """Strip ANSI escapes, zero-width and control characters (keeping newlines and tabs); truncate."""
    cleaned = _CONTROL.sub("", _ZERO_WIDTH.sub("", _ANSI.sub("", text)))
    return cleaned[:limit]


def assert_no_pii(output: Any, secrets: Iterable[str]) -> None:
    """Fail if any run of 5+ consecutive digits of a secret number appears in `output` (tests use this)."""
    if isinstance(output, BaseModel):
        text = output.model_dump_json()
    elif isinstance(output, str):
        text = output
    else:
        text = json.dumps(output, default=str)
    runs = _DIGIT_RUN.findall(text)
    for secret in secrets:
        digits = _digits(secret)
        for i in range(len(digits) - 4):
            window = digits[i : i + 5]
            if any(window in run for run in runs):
                raise AssertionError(f"output contains 5+ digits of a protected number (…{digits[-4:]})")
