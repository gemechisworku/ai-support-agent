"""T-COMP-04 · COMP-R4, COMP-R7: the flag checks and `evaluate`, the scheduler's single entry point."""

import datetime as dt
from pathlib import Path

import pytest

from supportagent.compliance.config import load_rules
from supportagent.compliance.rules import check_flags, evaluate, facts_from_eligible
from supportagent.contracts.bank import ContactPreferences, EligibleLoan
from supportagent.contracts.compliance import ContactFacts, DenyReason, GateDecision

RULES = load_rules(Path(__file__).resolve().parents[2] / "data" / "config" / "contact_rules.yaml")
# 10:00 in New York (UTC-5 in November): inside the 08:00-21:00 window
NOW = dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)


def facts(**over: object) -> ContactFacts:
    base: dict[str, object] = {
        "loan_id": 1,
        "customer_id": 1,
        "timezone": "America/New_York",
        "dnc": False,
        "consent_voice_ai": True,
        "cease_contact": False,
        "dispute_open": False,
        "attempts_last_7d": 0,
        "last_conversation_at": None,
        "planned_at": NOW,
    }
    return ContactFacts(**{**base, **over})  # type: ignore[arg-type]


# --------------------------------------------------------------------------- check_flags


def test_clean_facts_have_no_flag_reasons() -> None:
    assert check_flags(facts()) == []


@pytest.mark.parametrize(
    ("flag", "value", "reason"),
    [
        ("dnc", True, DenyReason.dnc),
        ("consent_voice_ai", False, DenyReason.no_consent),
        ("cease_contact", True, DenyReason.cease_contact),
        ("dispute_open", True, DenyReason.dispute_open),
    ],
)
def test_each_flag_denies_with_its_own_reason(flag: str, value: bool, reason: DenyReason) -> None:
    assert check_flags(facts(**{flag: value})) == [reason]


def test_every_flag_at_once_lists_every_reason_in_a_stable_order() -> None:
    got = check_flags(facts(dnc=True, consent_voice_ai=False, cease_contact=True, dispute_open=True))
    assert got == [DenyReason.dnc, DenyReason.no_consent, DenyReason.cease_contact, DenyReason.dispute_open]


# --------------------------------------------------------------------------- evaluate


def test_a_clean_loan_in_the_window_is_allowed_and_stamped() -> None:
    decision = evaluate(facts(), NOW, RULES)
    assert decision.allowed is True
    assert decision.reasons == []
    assert decision.rules_version == "contact_rules@" + RULES.version  # COMP-R7
    assert decision.evaluated_at == NOW


def test_dnc_and_cease_contact_are_both_reported() -> None:
    decision = evaluate(facts(dnc=True, cease_contact=True), NOW, RULES)
    assert decision.allowed is False
    assert decision.reasons == [DenyReason.dnc, DenyReason.cease_contact]


def test_a_missing_timezone_fails_closed() -> None:
    decision = evaluate(facts(timezone=None), NOW, RULES)
    assert decision.allowed is False
    assert decision.reasons == [DenyReason.unknown_timezone]


def test_an_invalid_timezone_fails_closed() -> None:
    assert evaluate(facts(timezone="Mars/Olympus"), NOW, RULES).reasons == [DenyReason.unknown_timezone]


def test_reasons_from_every_check_are_combined_without_short_circuiting() -> None:
    late = dt.datetime(2026, 11, 10, 3, 0, tzinfo=dt.UTC)  # 22:00 in New York
    decision = evaluate(
        facts(dnc=True, attempts_last_7d=7, last_conversation_at=late - dt.timedelta(days=1)), late, RULES
    )
    assert decision.reasons == [
        DenyReason.dnc,
        DenyReason.outside_window,
        DenyReason.frequency_cap,
        DenyReason.cool_off,
    ]


def test_the_preferred_window_reason_comes_through() -> None:
    decision = evaluate(facts(preferred_window="18:00-20:00"), NOW, RULES)
    assert decision.reasons == [DenyReason.outside_preferred_window]


def test_a_naive_now_is_an_error_not_an_allow() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate(facts(), dt.datetime(2026, 11, 9, 10, 0), RULES)


@pytest.mark.parametrize("dnc", [False, True])
def test_allowed_is_true_iff_there_are_no_reasons(dnc: bool) -> None:
    decision = evaluate(facts(dnc=dnc), NOW, RULES)
    assert decision.allowed is (decision.reasons == [])
    with pytest.raises(ValueError, match="exactly when"):
        GateDecision(
            allowed=not decision.allowed, reasons=decision.reasons, rules_version="x", evaluated_at=NOW
        )


# --------------------------------------------------------------------------- personas


def test_stop_calling_sam_is_never_allowed() -> None:
    sam = facts(cease_contact=True, loan_id=3, customer_id=3)
    for hour in range(24):  # whatever the time of day
        decision = evaluate(sam, dt.datetime(2026, 11, 9, hour, 0, tzinfo=dt.UTC), RULES)
        assert decision.allowed is False
        assert DenyReason.cease_contact in decision.reasons


def test_a_loan_with_an_open_dispute_is_never_allowed() -> None:
    for hour in range(24):
        decision = evaluate(facts(dispute_open=True), dt.datetime(2026, 11, 9, hour, 0, tzinfo=dt.UTC), RULES)
        assert decision.allowed is False
        assert DenyReason.dispute_open in decision.reasons


def test_frank_at_ten_local_with_no_attempts_is_allowed() -> None:
    frank = facts(loan_id=2, customer_id=2, attempts_last_7d=0, last_conversation_at=None)
    assert evaluate(frank, NOW, RULES).allowed is True


# --------------------------------------------------------------------------- facts_from_eligible


def eligible(**prefs: object) -> EligibleLoan:
    base: dict[str, object] = {
        "customer_id": 7,
        "consent_voice_ai": True,
        "dnc": False,
        "preferred_window": "09:00-12:00",
        "cease_contact": False,
        "dispute_open": False,
        "updated_at": NOW,
    }
    return EligibleLoan.model_validate(
        {
            "loan_id": 70,
            "customer_id": 7,
            "product_code": "PERSONAL",
            "timezone": "America/Chicago",
            "phone": "+15550100307",
            "days_past_due": 5,
            "bucket": "1-30",
            "next_due_date": dt.date(2026, 11, 20),
            "amount_due": "200.72",
            "prefs": ContactPreferences.model_validate({**base, **prefs}),
            "attempts_last_7d": 3,
            "last_conversation_at": NOW - dt.timedelta(days=9),
        }
    )


def test_facts_from_eligible_maps_every_field() -> None:
    planned = NOW + dt.timedelta(hours=2)
    got = facts_from_eligible(eligible(dnc=True, dispute_open=True), planned)
    assert got == ContactFacts(
        loan_id=70,
        customer_id=7,
        timezone="America/Chicago",
        preferred_window="09:00-12:00",
        dnc=True,
        consent_voice_ai=True,
        cease_contact=False,
        dispute_open=True,
        attempts_last_7d=3,
        last_conversation_at=NOW - dt.timedelta(days=9),
        planned_at=planned,
    )


def test_a_candidate_with_a_cease_flag_is_listed_but_the_gate_denies_it() -> None:
    got = facts_from_eligible(eligible(cease_contact=True), NOW)
    assert evaluate(got, NOW, RULES).reasons == [DenyReason.cease_contact]
