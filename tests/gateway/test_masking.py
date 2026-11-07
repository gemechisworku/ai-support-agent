"""T-GW-03 · GW-R5, GW-R10: masking, speakable formatting, text sanitising and the PII guard."""

import datetime as dt
from decimal import Decimal

import pytest

from supportagent.gateway.masking import (
    MAX_TEXT,
    assert_no_pii,
    mask_number,
    mask_phone,
    sanitize_text,
    speakable_date,
    speakable_money,
)


@pytest.mark.parametrize(
    ("raw", "masked"),
    [("5520264417", "****4417"), ("55-2026-4417", "****4417"), ("12345", "****2345"), ("1234", "****"),
     ("", "****"), (None, None)],
)  # fmt: skip
def test_mask_number(raw: str | None, masked: str | None) -> None:
    assert mask_number(raw) == masked


@pytest.mark.parametrize(
    ("raw", "masked"),
    [("+15550100302", "***-***-0302"), ("123", "***-***-****"), (None, None)],
)
def test_mask_phone(raw: str | None, masked: str | None) -> None:
    assert mask_phone(raw) == masked


@pytest.mark.parametrize(
    ("value", "spoken"),
    [(Decimal("245.1"), "$245.10"), (Decimal("1245.105"), "$1,245.11"), (Decimal("0.004"), "$0.00"),
     (Decimal("2.675"), "$2.68"), (Decimal("1000000"), "$1,000,000.00")],
)  # fmt: skip
def test_speakable_money_rounds_half_up(value: Decimal, spoken: str) -> None:
    assert speakable_money(value) == spoken


def test_speakable_date() -> None:
    assert speakable_date(dt.date(2026, 11, 4)) == "4 November 2026"
    assert speakable_date(dt.date(2026, 12, 25)) == "25 December 2026"


def test_sanitize_text_strips_hidden_characters_and_truncates() -> None:
    dirty = "Late\u200b fee\x1b[31m applies\x1b[0m.\x00\x07\nNext line\tok\ufeff"
    assert sanitize_text(dirty) == "Late fee applies.\nNext line\tok"
    assert len(sanitize_text("x" * 5000)) == MAX_TEXT
    assert sanitize_text("abc", limit=2) == "ab"


def test_assert_no_pii_flags_five_digit_runs() -> None:
    secrets = ["5520264417", "+15550100302"]
    assert_no_pii({"loan": "****4417", "phone": "***-***-0302", "amount": "245.10"}, secrets)
    with pytest.raises(AssertionError):
        assert_no_pii({"note": "loan 5520264417"}, secrets)
    with pytest.raises(AssertionError):
        assert_no_pii("ref 26441 is fine?", secrets)  # 5 digits from the loan number
    with pytest.raises(AssertionError):
        assert_no_pii({"x": "call 5550100302"}, secrets)
