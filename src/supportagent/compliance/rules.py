"""The contact-gate checks (COMP-R2, COMP-R3): small pure functions `(facts, now, rules) -> list[DenyReason]`.

`now` is injected and must be timezone-aware (determinism; constitution III). An empty list means the check
passes. Every check fails closed: missing or invalid facts produce a reason, never an allow.

    decision = evaluate(facts, now, rules)  # the scheduler's single entry point (T-SCHED-01, T-SCHED-04)
"""

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from supportagent.contracts.bank import EligibleLoan
from supportagent.contracts.compliance import ContactFacts, ContactRules, DenyReason, GateDecision


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


def check_flags(facts: ContactFacts) -> list[DenyReason]:
    """COMP-R4: the customer's own flags. Each one denies on its own, and all that apply are listed."""
    reasons: list[DenyReason] = []
    if facts.dnc:
        reasons.append(DenyReason.dnc)
    if not facts.consent_voice_ai:
        reasons.append(DenyReason.no_consent)
    if facts.cease_contact:
        reasons.append(DenyReason.cease_contact)
    if facts.dispute_open:
        reasons.append(DenyReason.dispute_open)
    return reasons


def evaluate(facts: ContactFacts, now: dt.datetime, rules: ContactRules) -> GateDecision:
    """COMP-R4, COMP-R7: may we dial this loan at `now`? Runs every check and lists every reason (no
    short-circuit, so the console and audit see them all). Fails closed: a missing or invalid timezone is
    `unknown_timezone`, and a naive `now` raises. The decision is the audit record: reasons, the rules
    version (`contact_rules@<version>`) and when it was evaluated. It holds no contact data."""
    _require_aware(now)
    reasons = (
        check_flags(facts) + check_calling_window(facts, now, rules) + check_frequency(facts, now, rules)
    )
    return GateDecision(
        allowed=not reasons,
        reasons=reasons,
        rules_version=rules.version_tag,
        evaluated_at=now,
    )


def facts_from_eligible(loan: EligibleLoan, planned_at: dt.datetime) -> ContactFacts:
    """Gate facts for one scheduler candidate (`GET /service/candidates`) at the time it plans to dial."""
    prefs = loan.prefs
    return ContactFacts(
        loan_id=loan.loan_id,
        customer_id=loan.customer_id,
        timezone=loan.timezone,
        preferred_window=prefs.preferred_window,
        dnc=prefs.dnc,
        consent_voice_ai=prefs.consent_voice_ai,
        cease_contact=prefs.cease_contact,
        dispute_open=prefs.dispute_open,
        attempts_last_7d=loan.attempts_last_7d,
        last_conversation_at=loan.last_conversation_at,
        planned_at=planned_at,
    )
