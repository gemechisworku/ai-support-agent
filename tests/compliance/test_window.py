"""T-COMP-02 · COMP-R2: the calling window in the customer's local time (DST-correct, fail closed)."""

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from supportagent.compliance.config import load_rules
from supportagent.compliance.rules import check_calling_window
from supportagent.contracts.compliance import ContactFacts, DenyReason

RULES = load_rules(Path(__file__).resolve().parents[2] / "data" / "config" / "contact_rules.yaml")
UTC = dt.UTC
LA, NY = "America/Los_Angeles", "America/New_York"


def facts(timezone: str | None = LA, preferred: str | None = None) -> ContactFacts:
    return ContactFacts(
        loan_id=1,
        customer_id=1,
        timezone=timezone,
        preferred_window=preferred,
        dnc=False,
        consent_voice_ai=True,
        cease_contact=False,
        dispute_open=False,
        attempts_last_7d=0,
        planned_at=dt.datetime(2026, 11, 9, 12, 0, tzinfo=UTC),
    )


def at(y: int, mo: int, d: int, h: int, mi: int = 0) -> dt.datetime:
    return dt.datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_spec_example_los_angeles() -> None:
    assert check_calling_window(facts(), at(2026, 11, 9, 15, 30), RULES) == [
        DenyReason.outside_window
    ]  # 07:30
    assert check_calling_window(facts(), at(2026, 11, 9, 16, 0), RULES) == []  # 08:00


@pytest.mark.parametrize(
    ("local", "allowed"),
    [((7, 59), False), ((8, 0), True), ((20, 59), True), ((21, 0), False), ((0, 0), False), ((12, 0), True)],
)
def test_boundaries_start_inclusive_end_exclusive(local: tuple[int, int], allowed: bool) -> None:
    now = dt.datetime(2026, 11, 9, *local, tzinfo=ZoneInfo(NY))
    assert (check_calling_window(facts(NY), now, RULES) == []) is allowed


def test_zones_are_independent() -> None:
    now = at(2026, 11, 9, 14, 0)  # 06:00 LA, 09:00 NY, 14:00 UTC
    assert check_calling_window(facts(LA), now, RULES) == [DenyReason.outside_window]
    assert check_calling_window(facts(NY), now, RULES) == []
    assert check_calling_window(facts("UTC"), now, RULES) == []


def test_spring_forward_2026_03_08_new_york() -> None:
    # 02:00 EST -> 03:00 EDT. 07:59 EDT is 11:59Z; 08:00 EDT is 12:00Z (an hour earlier than in winter).
    assert check_calling_window(facts(NY), at(2026, 3, 8, 11, 59), RULES) == [DenyReason.outside_window]
    assert check_calling_window(facts(NY), at(2026, 3, 8, 12, 0), RULES) == []
    # The evening edge: 21:00 EDT is 01:00Z next day.
    assert check_calling_window(facts(NY), at(2026, 3, 9, 0, 59), RULES) == []
    assert check_calling_window(facts(NY), at(2026, 3, 9, 1, 0), RULES) == [DenyReason.outside_window]


def test_fall_back_2026_11_01_los_angeles() -> None:
    # 02:00 PDT -> 01:00 PST. 08:00 PST is 16:00Z (07:00 PDT would have been 14:00Z).
    assert check_calling_window(facts(LA), at(2026, 11, 1, 15, 59), RULES) == [DenyReason.outside_window]
    assert check_calling_window(facts(LA), at(2026, 11, 1, 16, 0), RULES) == []
    assert check_calling_window(facts(LA), at(2026, 11, 2, 4, 59), RULES) == []  # 20:59 PST
    assert check_calling_window(facts(LA), at(2026, 11, 2, 5, 0), RULES) == [DenyReason.outside_window]


def test_preferred_window_inside_and_outside() -> None:
    pref = "18:00-20:00"
    assert check_calling_window(facts(LA, pref), at(2026, 11, 10, 2, 0), RULES) == []  # 18:00 PST
    assert check_calling_window(facts(LA, pref), at(2026, 11, 10, 3, 59), RULES) == []  # 19:59
    assert check_calling_window(facts(LA, pref), at(2026, 11, 10, 4, 0), RULES) == [
        DenyReason.outside_preferred_window  # 20:00 PST, end exclusive
    ]
    # The spec example: 12:00 local is outside 18:00-20:00 though inside the general window.
    assert check_calling_window(facts(LA, pref), at(2026, 11, 9, 20, 0), RULES) == [
        DenyReason.outside_preferred_window
    ]


def test_the_general_window_wins_when_both_are_violated() -> None:
    # 06:00 local is outside both; only the general window is reported (one reason per check).
    assert check_calling_window(facts(LA, "05:00-07:00"), at(2026, 11, 9, 14, 0), RULES) == [
        DenyReason.outside_window
    ]


@pytest.mark.parametrize("zone", [None, "", "Mars/Olympus", "../etc/passwd", "not a zone"])
def test_missing_or_invalid_zone_fails_closed(zone: str | None) -> None:
    assert check_calling_window(facts(zone), at(2026, 11, 9, 16, 0), RULES) == [DenyReason.unknown_timezone]


def test_a_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        check_calling_window(facts(), dt.datetime(2026, 11, 9, 16, 0), RULES)


@pytest.mark.parametrize(
    "bad", ["20:00-18:00", "18:00-18:00", "9:00-10:00", "18:00", "ab:cd-ef:gh", "18:00-25:00"]
)
def test_a_malformed_preferred_window_fails_at_fact_construction(bad: str) -> None:
    with pytest.raises(ValueError):
        facts(LA, bad)


def test_the_window_comes_from_the_rules() -> None:
    narrow = RULES.model_copy(
        update={"calling_window": RULES.calling_window.model_copy(update={"end": dt.time(9, 0)})}
    )
    assert check_calling_window(facts(NY), at(2026, 11, 9, 14, 30), narrow) == [
        DenyReason.outside_window
    ]  # 09:30


def test_allowed_implies_the_local_hour_is_inside_the_window_everywhere() -> None:
    """Exhaustive stand-in for a property test: every 5 minutes over the two 2026 DST weekends, five zones."""
    zones = [LA, NY, "UTC", "Europe/London", "Asia/Kolkata"]
    for start in (dt.datetime(2026, 3, 7, tzinfo=UTC), dt.datetime(2026, 10, 31, tzinfo=UTC)):
        for step in range(0, 4 * 24 * 12):
            now = start + dt.timedelta(minutes=5 * step)
            for zone in zones:
                local = now.astimezone(ZoneInfo(zone))
                allowed = check_calling_window(facts(zone), now, RULES) == []
                assert allowed == (8 <= local.hour < 21), (zone, now)
