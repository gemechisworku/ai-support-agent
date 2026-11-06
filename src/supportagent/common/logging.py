"""Structured JSON logging with PII masking by key (PLAT-R2).

One JSON object per line: `ts` (UTC ISO-8601), `level`, `logger`, `msg`, `call_id` (when bound),
`exc` (when logging an exception), plus every field passed in `extra=`. Fields whose key names PII
are masked before the record is written::

    log = get_logger(__name__)
    with bind_call(call_id):
        log.info("lookup", extra={"phone": "+15551234567", "loan_id": 7})
    # {"ts": "...", "level": "INFO", "logger": "...", "msg": "lookup",
    #  "call_id": "...", "phone": "***4567", "loan_id": 7}

Put data in `extra=`, never in the message text: only structured fields are masked by key.
Free-text redaction (transcripts, summaries) is `supportagent.observability` (T-OBS-01).
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import IO, Any

MASK = "***"

# Keys masked entirely.
_PII_KEYS = {
    "full_name",
    "name",
    "first_name",
    "last_name",
    "dob",
    "date_of_birth",
    "email",
    "address",
}
# Keys masked to their last 4 characters (enough to tell records apart, not enough to identify).
_LAST4_KEYS = {"phone", "phone_number", "account_number", "loan_number", "national_id"}
_LAST4_SUFFIXES = ("_phone",)
_PII_SUFFIXES = ("_email",)

# Attributes every LogRecord has; anything else on a record came from `extra=`.
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}

_call_id: ContextVar[str | None] = ContextVar("call_id", default=None)


def _mask_value(key: str, value: Any) -> Any:
    k = key.lower()
    if k in _LAST4_KEYS or k.endswith(_LAST4_SUFFIXES):
        if value is None:
            return None
        text = str(value)
        return MASK + text[-4:] if len(text) > 4 else MASK
    if k in _PII_KEYS or k.endswith(_PII_SUFFIXES):
        return None if value is None else MASK
    return mask_pii(value)


def mask_pii(value: Any) -> Any:
    """Return a copy of `value` with PII-named keys masked, recursing into mappings and sequences."""
    if isinstance(value, Mapping):
        return {k: _mask_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [mask_pii(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    """Formats a record as one JSON line, masking PII in `extra=` fields. Never raises."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        call_id = _call_id.get()
        if call_id is not None:
            out["call_id"] = call_id
        extras = {k: v for k, v in vars(record).items() if k not in _RESERVED and k not in out}
        out.update(mask_pii(extras))
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        try:
            return json.dumps(out, default=_fallback, ensure_ascii=False)
        except (TypeError, ValueError):  # e.g. circular references
            return json.dumps({k: _fallback(v) for k, v in out.items()}, ensure_ascii=False)


def _fallback(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    try:
        return repr(value)
    except Exception:
        return f"<unrepresentable {type(value).__name__}>"


def configure_logging(level: str | int = "INFO", stream: IO[str] | None = None) -> None:
    """Install the JSON handler on the root logger. Safe to call more than once (replaces itself)."""
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_supportagent", False)]:
        root.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler._supportagent = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextmanager
def bind_call(call_id: str) -> Iterator[None]:
    """Add `call_id` to every record logged in this context (per asyncio task / thread)."""
    token = _call_id.set(call_id)
    try:
        yield
    finally:
        _call_id.reset(token)
