"""T-COMP-03 · COMP-R3: the 7-in-7 frequency cap and the post-conversation cool-off (limits from config)."""

import datetime as dt
from pathlib import Path

import pytest

from supportagent.compliance.config import load_rules
from supportagent.compliance.rules import check_frequency
from supportagent.contracts.compliance import ContactFacts, ContactRules, DenyReason, FrequencyCap

RULES = load_rules(Path(__file__).resolve().parents[2] / "data" / "config" / "contact_rules.yaml")
NOW = dt.datetime(2026, 11, 9, 16, 0, tzinfo=dt.UTC)


def facts(attempts: int = 0, last: dt.datetime | None = None) -> ContactFacts:
    return ContactFacts(
        loan_id=1,
        customer_id=1,
        timezone="America/New_York",
        dnc=False,
        consent_voice_ai=True,
        cease_contact=False,
        dispute_open=False,
        attempts_last_7d=attempts,
        last_conversation_at=last,
        planned_at=NOW,
    )


def rules_with(**frequency: int) -> ContactRules:
    base = RULES.frequency.model_dump()
    return RULES.model_copy(update={"frequency": FrequencyCap(**{**base, **frequency})})


@pytest.mark.parametrize(("attempts", "denied"), [(0, False), (6, False), (7, True), (8, True), (50, True)])
def test_the_cap_is_reached_at_max_attempts(attempts: int, denied: bool) -> None:
    expected = [DenyReason.frequency_cap] if denied else []
    assert check_frequency(facts(attempts), NOW, RULES) == expected


def test_cool_off_6_days_23_hours_denies_and_7_days_1_hour_allows() -> None:
    assert check_frequency(facts(last=NOW - dt.timedelta(days=6, hours=23)), NOW, RULES) == [
        DenyReason.cool_off
    ]
    assert check_frequency(facts(last=NOW - dt.timedelta(days=7, hours=1)), NOW, RULES) == []


def test_the_cool_off_boundary_is_exactly_the_configured_days() -> None:
    assert check_frequency(facts(last=NOW - dt.timedelta(days=7)), NOW, RULES) == []  # 7 days ago: over
    just_inside = NOW - dt.timedelta(days=7) + dt.timedelta(seconds=1)
    assert check_frequency(facts(last=just_inside), NOW, RULES) == [DenyReason.cool_off]


def test_no_previous_conversation_means_no_cool_off() -> None:
    assert check_frequency(facts(attempts=0, last=None), NOW, RULES) == []


def test_both_reasons_are_reported_together() -> None:
    result = check_frequency(facts(7, NOW - dt.timedelta(days=1)), NOW, RULES)
    assert result == [DenyReason.frequency_cap, DenyReason.cool_off]


def test_a_conversation_in_the_future_still_counts_as_recent() -> None:
    """Clock skew must not open the gate: a negative age is below the cool-off."""
    assert check_frequency(facts(last=NOW + dt.timedelta(hours=1)), NOW, RULES) == [DenyReason.cool_off]


def test_custom_limits_change_behaviour_without_code_changes() -> None:
    strict = rules_with(max_attempts=3, cool_off_days_after_conversation=14)
    assert check_frequency(facts(3), NOW, strict) == [DenyReason.frequency_cap]
    assert check_frequency(facts(3), NOW, RULES) == []
    ten_days = NOW - dt.timedelta(days=10)
    assert check_frequency(facts(last=ten_days), NOW, strict) == [DenyReason.cool_off]
    assert check_frequency(facts(last=ten_days), NOW, RULES) == []


def test_a_zero_day_cool_off_disables_it() -> None:
    off = rules_with(cool_off_days_after_conversation=0)
    assert check_frequency(facts(last=NOW - dt.timedelta(seconds=1)), NOW, off) == []


def test_a_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        check_frequency(facts(), dt.datetime(2026, 11, 9, 16, 0), RULES)


def test_the_yaml_limits_are_what_the_spec_says() -> None:
    assert (RULES.frequency.max_attempts, RULES.frequency.cool_off_days_after_conversation) == (7, 7)
