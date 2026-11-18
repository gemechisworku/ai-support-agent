"""T-COMP-05 · COMP-R6: after-the-fact check that a call gave its disclosures and revealed no data early."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from supportagent.compliance.disclosures import (
    ACCOUNT_DATA_TOOLS,
    DisclosureRegistry,
    check_disclosures,
    load_disclosures,
)
from supportagent.contracts.compliance import CallEvent
from supportagent.contracts.outcome import CallOutcome

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def registry() -> DisclosureRegistry:
    return load_disclosures(ROOT / "data" / "config" / "disclosures.yaml")


def outcome(**overrides: Any) -> CallOutcome:
    base: dict[str, Any] = {
        "call_id": "3f2b8c1e-5d4a-4b6f-9a7e-2c1d0e9f8a7b",
        "direction": "outbound",
        "campaign": "missed_payment",
        "contact_result": "right_party",
        "verified": True,
        "disclosures_given": ["ai_recording@1.0", "collections@1.0"],
        "versions": {"prompt": "p", "policy": "k", "model": "m"},
    }
    base.update(overrides)
    return CallOutcome.model_validate(base)


def stream(*items: tuple[str, str]) -> list[CallEvent]:
    """Events one second apart, in the given order."""
    return [
        CallEvent(ts=T0 + timedelta(seconds=i), kind=kind, name=name) for i, (kind, name) in enumerate(items)
    ]  # type: ignore[arg-type]


COMPLIANT_U2 = stream(
    ("disclosure", "ai_recording"),
    ("tool", "verify_identity"),
    ("verified", ""),
    ("disclosure", "collections"),
    ("tool", "get_loan_status"),
    ("tool", "search_policy"),
    ("tool", "record_promise_to_pay"),
)


def codes(o: CallOutcome, events: list[CallEvent], registry: DisclosureRegistry) -> list[str]:
    return [v.code for v in check_disclosures(o, events, registry)]


def test_compliant_missed_payment_call(registry: DisclosureRegistry) -> None:
    assert check_disclosures(outcome(), COMPLIANT_U2, registry) == []


def test_missing_collections_disclosure(registry: DisclosureRegistry) -> None:
    o = outcome(disclosures_given=["ai_recording@1.0"])
    assert codes(o, COMPLIANT_U2, registry) == ["missing_disclosure:collections"]


def test_missing_ai_disclosure(registry: DisclosureRegistry) -> None:
    o = outcome(disclosures_given=["collections@1.0"])
    violations = check_disclosures(o, COMPLIANT_U2, registry)
    assert [v.code for v in violations] == ["missing_disclosure:ai_recording"]
    assert "ai_recording" in violations[0].detail


def test_data_before_verification(registry: DisclosureRegistry) -> None:
    events = stream(
        ("disclosure", "ai_recording"),
        ("tool", "get_loan_status"),
        ("verified", ""),
        ("disclosure", "collections"),
    )
    assert codes(outcome(), events, registry) == [
        "data_before_verification",
        "data_before_collections_disclosure",
    ]


def test_data_without_any_verification(registry: DisclosureRegistry) -> None:
    o = outcome(campaign="pre_due", verified=False, disclosures_given=["ai_recording@1.0"])
    events = stream(("disclosure", "ai_recording"), ("tool", "get_recent_transactions"))
    assert codes(o, events, registry) == ["data_before_verification"]


def test_data_before_collections_disclosure(registry: DisclosureRegistry) -> None:
    events = stream(
        ("disclosure", "ai_recording"),
        ("verified", ""),
        ("tool", "get_interaction_history"),
        ("disclosure", "collections"),
    )
    assert codes(outcome(), events, registry) == ["data_before_collections_disclosure"]


def test_each_violation_reported_once(registry: DisclosureRegistry) -> None:
    events = stream(*[("tool", t) for t in sorted(ACCOUNT_DATA_TOOLS)], ("verified", ""))
    o = outcome(disclosures_given=[])
    assert codes(o, events, registry) == [
        "missing_disclosure:ai_recording",
        "missing_disclosure:collections",
        "data_before_verification",
        "data_before_collections_disclosure",
    ]


def test_events_are_ordered_by_time_not_list_position(registry: DisclosureRegistry) -> None:
    late_tool = CallEvent(ts=T0 + timedelta(minutes=5), kind="tool", name="get_loan_status")
    assert check_disclosures(outcome(), [late_tool, *COMPLIANT_U2[:4]], registry) == []


def test_pre_due_call_needs_no_collections_disclosure(registry: DisclosureRegistry) -> None:
    o = outcome(campaign="pre_due", disclosures_given=["ai_recording@1.0"])
    events = stream(("disclosure", "ai_recording"), ("verified", ""), ("tool", "get_loan_status"))
    assert check_disclosures(o, events, registry) == []


def test_inbound_policy_only_call_needs_no_verification(registry: DisclosureRegistry) -> None:
    o = outcome(
        direction="inbound", campaign="inbound", verified=False, disclosures_given=["ai_recording@1.0"]
    )
    events = stream(("disclosure", "ai_recording"), ("tool", "search_policy"), ("utterance", "agent"))
    assert check_disclosures(o, events, registry) == []


def test_unverified_u2_call_does_not_need_collections(registry: DisclosureRegistry) -> None:
    """Verification failed: no amount may be stated, so the collections disclosure is not owed."""
    o = outcome(verified=False, disclosures_given=["ai_recording@1.0"])
    events = stream(("disclosure", "ai_recording"), ("tool", "verify_identity"), ("tool", "verify_identity"))
    assert check_disclosures(o, events, registry) == []


def test_third_party_call(registry: DisclosureRegistry) -> None:
    o = outcome(contact_result="third_party", verified=False, disclosures_given=["ai_recording@1.0"])
    assert check_disclosures(o, stream(("disclosure", "ai_recording")), registry) == []
    assert codes(o.model_copy(update={"disclosures_given": []}), [], registry) == [
        "missing_disclosure:ai_recording"
    ]


@pytest.mark.parametrize("result", ["no_answer", "voicemail", "busy", "wrong_number"])
def test_no_conversation_owes_no_disclosure(registry: DisclosureRegistry, result: str) -> None:
    o = outcome(contact_result=result, verified=False, disclosures_given=[])
    assert check_disclosures(o, [], registry) == []


def test_default_registry_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    from supportagent.compliance import disclosures

    monkeypatch.setattr(
        disclosures, "default_disclosures_path", lambda: ROOT / "data/config/disclosures.yaml"
    )
    assert check_disclosures(outcome(), COMPLIANT_U2) == []
