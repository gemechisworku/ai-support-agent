"""The contact-gate checks (COMP-R2, COMP-R3): small pure functions `(facts, now, rules) -> list[DenyReason]`.

`now` is injected and must be timezone-aware (determinism; constitution III). An empty list means the check
passes. Every check fails closed: missing or invalid facts produce a reason, never an allow.

    reasons = check_calling_window(facts, now, rules) + check_frequency(facts, now, rules)
"""

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from supportagent.contracts.compliance import ContactFacts, ContactRules, DenyReason


def _require_aware(now: dt.datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):  # unknown, malformed or path-like names
        return None


def _hhmm(text: str) -> dt.time:
    hour, minute = text.split(":")
    return dt.time(int(hour), int(minute))


def check_calling_window(facts: ContactFacts, now: dt.datetime, rules: ContactRules) -> list[DenyReason]:
    """COMP-R2: outbound calls only inside `[start, end)` of the customer's local day, and inside their
    `preferred_window` (same day, `[start, end)`) when one is set. A missing or invalid zone is
    `unknown_timezone`, and no window can be judged."""
    _require_aware(now)
    zone = _zone(facts.timezone)
    if zone is None:
        return [DenyReason.unknown_timezone]
    local = now.astimezone(zone).time().replace(tzinfo=None)  # wall-clock time, DST-correct
    window = rules.calling_window
    if not window.start <= local < window.end:
        return [DenyReason.outside_window]
    if facts.preferred_window is not None:
        start, _, end = facts.preferred_window.partition("-")
        if not _hhmm(start) <= local < _hhmm(end):
            return [DenyReason.outside_preferred_window]
    return []


def check_frequency(facts: ContactFacts, now: dt.datetime, rules: ContactRules) -> list[DenyReason]:
    """COMP-R3: `frequency_cap` when attempts in the trailing window reach `max_attempts`; `cool_off` when a
    right-party conversation happened less than `cool_off_days_after_conversation` ago. Both limits come from
    the rules, and both reasons can apply at once."""
    _require_aware(now)
    reasons: list[DenyReason] = []
    if facts.attempts_last_7d >= rules.frequency.max_attempts:
        reasons.append(DenyReason.frequency_cap)
    last = facts.last_conversation_at
    if last is not None and now - last < dt.timedelta(days=rules.frequency.cool_off_days_after_conversation):
        reasons.append(DenyReason.cool_off)
    return reasons
