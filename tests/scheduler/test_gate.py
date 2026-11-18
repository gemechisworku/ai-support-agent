"""T-SCHED-01 · SCHED-R2: every candidate passes the compliance gate; an exclusion lists every reason."""

import datetime as dt
from itertools import count

import pytest

from supportagent.contracts.compliance import ContactRules, DenyReason
from supportagent.contracts.gateway import Candidate
from supportagent.contracts.scheduler import CallJob, Exclusion
from supportagent.scheduler.gate import admit, facts_from_candidate
from tests.scheduler.conftest import NOW, candidate, dawit, frank, prefs, sam

_ids = count(1)


def new_id() -> str:
    return f"00000000-0000-4000-8000-{next(_ids):012d}"


def run(c: Candidate, rules: ContactRules, now: dt.datetime = NOW) -> CallJob | Exclusion:
    return admit(c, now, rules, new_id=new_id)


def reasons(result: CallJob | Exclusion) -> list[str]:
    assert isinstance(result, Exclusion), result
    return result.reasons


def test_an_allowed_candidate_becomes_a_queued_job(rules: ContactRules) -> None:
    job = run(frank(), rules)
    assert isinstance(job, CallJob)
    assert (job.customer_id, job.loan_id, job.campaign) == (1, 11, "missed_payment")
    assert job.status == "queued" and job.attempt_no == 1 and job.reasons == []
    assert job.not_before == NOW
    assert job.call_id != job.job_id  # call_id is a UUID4 created at queue time


def test_sam_is_excluded_for_cease_contact(rules: ContactRules) -> None:
    assert reasons(run(sam(), rules)) == ["cease_contact"]


def test_dawit_is_excluded_for_an_open_dispute(rules: ContactRules) -> None:
    assert reasons(run(dawit(), rules)) == ["dispute_open"]


def test_a_dnc_customer_is_excluded(rules: ContactRules) -> None:
    assert reasons(run(candidate(6, 61, dpd=4, contact=prefs(dnc=True)), rules)) == ["dnc"]


def test_a_customer_with_no_consent_is_excluded(rules: ContactRules) -> None:
    c = candidate(7, 71, dpd=4, contact=prefs(consent_voice_ai=False))
    assert reasons(run(c, rules)) == ["no_consent"]


def test_21_30_local_time_is_outside_the_window(rules: ContactRules) -> None:
    at = dt.datetime(2026, 11, 10, 2, 30, tzinfo=dt.UTC)  # 21:30 in New York
    assert reasons(run(frank(), rules, at)) == ["outside_window"]


def test_a_missing_timezone_fails_closed(rules: ContactRules) -> None:
    assert reasons(run(candidate(8, 81, dpd=4, timezone=None), rules)) == ["unknown_timezone"]


def test_the_frequency_cap_and_cool_off_exclude(rules: ContactRules) -> None:
    capped = candidate(9, 91, dpd=4, attempts=rules.frequency.max_attempts)
    assert reasons(run(capped, rules)) == ["frequency_cap"]
    talked = candidate(10, 101, dpd=4, last_conversation_at=NOW - dt.timedelta(days=1))
    assert reasons(run(talked, rules)) == ["cool_off"]


def test_every_failing_rule_is_recorded_not_just_the_first(rules: ContactRules) -> None:
    c = candidate(
        11,
        111,
        dpd=4,
        contact=prefs(dnc=True, cease_contact=True, dispute_open=True, consent_voice_ai=False),
        attempts=rules.frequency.max_attempts,
        last_conversation_at=NOW - dt.timedelta(hours=2),
    )
    at = dt.datetime(2026, 11, 10, 2, 30, tzinfo=dt.UTC)
    result = run(c, rules, at)
    assert reasons(result) == [
        DenyReason.dnc,
        DenyReason.no_consent,
        DenyReason.cease_contact,
        DenyReason.dispute_open,
        DenyReason.outside_window,
        DenyReason.frequency_cap,
        DenyReason.cool_off,
    ]
    assert isinstance(result, Exclusion) and result.at == at


def test_the_exclusion_names_the_candidate_and_campaign(rules: ContactRules) -> None:
    ex = run(sam(), rules)
    assert isinstance(ex, Exclusion)
    assert (ex.customer_id, ex.loan_id, ex.campaign) == (4, 41, "missed_payment")


def test_facts_carry_the_candidates_gate_inputs() -> None:
    c = candidate(12, 121, dpd=4, contact=prefs(dnc=True), attempts=3)
    facts = facts_from_candidate(c, NOW)
    assert (facts.loan_id, facts.customer_id, facts.timezone) == (121, 12, "America/New_York")
    assert facts.dnc is True and facts.attempts_last_7d == 3 and facts.planned_at == NOW


def test_a_naive_clock_is_rejected(rules: ContactRules) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        run(frank(), rules, dt.datetime(2026, 11, 9, 15, 0))
