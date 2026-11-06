"""T-PLAT-02 · PLAT-R2: JSON logging that masks PII by key before a record is emitted."""

import io
import json
import logging
from collections.abc import Iterator
from datetime import date

import pytest

from supportagent.common.logging import bind_call, configure_logging, get_logger, mask_pii


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    buf = io.StringIO()
    configure_logging("DEBUG", stream=buf)
    yield buf
    configure_logging("INFO")  # restore a plain stderr handler


def _records(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def test_mask_by_key_keeps_last_four_for_numbers() -> None:
    out = mask_pii(
        {
            "phone": "+15551234567",
            "loan_number": "LN-0000-7788",
            "account_number": "123",
            "full_name": "Frank Forgetful",
            "dob": date(1980, 1, 2),
            "email": "f@example.com",
            "loan_id": 7,
        }
    )
    assert out == {
        "phone": "***4567",
        "loan_number": "***7788",
        "account_number": "***",
        "full_name": "***",
        "dob": "***",
        "email": "***",
        "loan_id": 7,
    }


def test_mask_is_recursive_and_case_insensitive() -> None:
    out = mask_pii(
        {"customer": {"Name": "Hana", "loans": [{"loan_number": "9999-1234"}]}, "caller_phone": "5550001111"}
    )
    assert out == {
        "customer": {"Name": "***", "loans": [{"loan_number": "***1234"}]},
        "caller_phone": "***1111",
    }


def test_mask_does_not_mutate_input() -> None:
    data = {"phone": "+15551234567"}
    mask_pii(data)
    assert data == {"phone": "+15551234567"}


def test_json_shape_and_masking(stream: io.StringIO) -> None:
    get_logger("supportagent.test").info("lookup", extra={"phone": "+15551234567", "loan_id": 7})
    (rec,) = _records(stream)
    assert rec["level"] == "INFO"
    assert rec["logger"] == "supportagent.test"
    assert rec["msg"] == "lookup"
    assert rec["ts"].endswith("+00:00")
    assert rec["phone"] == "***4567"
    assert rec["loan_id"] == 7
    assert "call_id" not in rec


def test_bind_call_adds_call_id_in_context(stream: io.StringIO) -> None:
    log = get_logger("supportagent.test")
    with bind_call("c0ffee00-0000-4000-8000-000000000001"):
        log.info("inside")
    log.info("outside")
    inside, outside = _records(stream)
    assert inside["call_id"] == "c0ffee00-0000-4000-8000-000000000001"
    assert "call_id" not in outside


def test_unserialisable_values_do_not_raise(stream: io.StringIO) -> None:
    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    get_logger("supportagent.test").info("odd", extra={"thing": Opaque(), "when": date(2026, 11, 9)})
    (rec,) = _records(stream)
    assert rec["thing"] == "<opaque>"
    assert rec["when"] == "2026-11-09"


def test_exceptions_are_included(stream: io.StringIO) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("supportagent.test").exception("failed")
    (rec,) = _records(stream)
    assert rec["level"] == "ERROR"
    assert "ValueError: boom" in rec["exc"]


def test_configure_logging_is_idempotent(stream: io.StringIO) -> None:
    configure_logging("DEBUG", stream=stream)
    get_logger("supportagent.test").info("once")
    assert len(_records(stream)) == 1
    assert sum(1 for h in logging.getLogger().handlers if getattr(h, "_supportagent", False)) == 1
