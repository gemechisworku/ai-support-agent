"""T-AGENT-08 · AGENT-R11 / AGENT-R12: the OutcomeBuilder and the post-call summary template."""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from supportagent.agent.outcome import LatencyFigures, OutcomeBuilder
from supportagent.agent.summary import (
    compose_summary,
    figures_in,
    format_date,
    format_money,
    readback_line,
    ungrounded_figures,
)
from supportagent.contracts.call import CallMetadata
from supportagent.contracts.outcome import (
    Callback,
    CallOutcome,
    Campaign,
    ContactResult,
    Direction,
    Escalation,
    Intent,
    PromiseToPay,
    ReasonCode,
    Sentiment,
    StageLatency,
    Versions,
)

VERSIONS = Versions(prompt="0.1.0+abc", policy="p-1", model="llm:llamacpp/test")
PROMISE_DATE = dt.date(2026, 11, 12)
CALLBACK_AT = dt.datetime(2026, 11, 13, 14, 30, tzinfo=dt.UTC)
BANK = "Meridian Bank"


def _builder(
    direction: Direction = Direction.outbound, campaign: Campaign = Campaign.missed_payment
) -> OutcomeBuilder:
    meta = CallMetadata(
        call_id=str(uuid.uuid4()), direction=direction, campaign=campaign, customer_id=9001, loan_id=1
    )
    return OutcomeBuilder(meta)


def _build(b: OutcomeBuilder, **kw: object) -> CallOutcome:
    args: dict[str, object] = {
        "verified": True,
        "contact_result": ContactResult.right_party,
        "disclosures_given": ["ai_recording@1.0", "collections@1.0"],
        "versions": VERSIONS,
        "bank_name": BANK,
    }
    args.update(kw)
    return b.build(**args)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- builder


def test_a_fresh_builder_builds_a_valid_minimal_outcome() -> None:
    b = _builder()
    out = _build(b, verified=False, contact_result=ContactResult.no_answer, disclosures_given=[])
    assert isinstance(out, CallOutcome)
    assert out.call_id == b.call_id and out.customer_id == 9001 and out.loan_id == 1
    assert (out.direction, out.campaign) == (Direction.outbound, Campaign.missed_payment)
    assert out.versions == VERSIONS
    assert out.intent == [] and out.promise_to_pay is None and out.sentiment is Sentiment.unknown


def test_the_builder_merges_what_the_states_record() -> None:
    b = _builder()
    b.set_reason_code(ReasonCode.forgot)
    b.record_promise(PROMISE_DATE, Decimal("200.72"))
    b.record_callback(CALLBACK_AT)
    b.add_intent(Intent.already_paid)
    b.add_intent(Intent.already_paid)  # a repeat is ignored
    b.add_kb_source("late-fees@1.0#Late fees")
    b.add_kb_source("late-fees@1.0#Late fees")
    b.set_latency(
        LatencyFigures(p50_ms=900, p90_ms=1500, stages={"e2e": StageLatency(p50_ms=900, p90_ms=1500)})
    )
    out = _build(b)
    assert out.reason_code is ReasonCode.forgot
    assert out.promise_to_pay == PromiseToPay(date=PROMISE_DATE, amount=Decimal("200.72"))
    assert out.callback == Callback(datetime=CALLBACK_AT)
    assert out.intent == [Intent.promise_to_pay, Intent.callback_request, Intent.already_paid]
    assert out.kb_sources == ["late-fees@1.0#Late fees"]
    assert (out.latency_p50, out.latency_p90) == (900, 1500)
    assert out.latency_stages["e2e"].p90_ms == 1500
    assert out.disclosures_given == ["ai_recording@1.0", "collections@1.0"]


@pytest.mark.parametrize(
    ("reason", "intent"),
    [
        ("hardship", Intent.hardship),
        ("dispute", Intent.dispute),
        ("complaint", Intent.complaint),
        ("legal", Intent.legal),
        ("human_requested", Intent.human_request),
    ],
)
def test_an_escalation_records_its_intent(reason: str, intent: Intent) -> None:
    b = _builder()
    b.record_escalation(reason, "200")
    out = _build(b)
    assert out.escalated == Escalation(reason=reason, target="200")
    assert out.intent == [intent]


def test_an_escalation_for_failed_verification_adds_no_intent() -> None:
    b = _builder()
    b.record_escalation("verification_failed", "callback_queue")
    out = _build(b, verified=False)
    assert out.escalated is not None and out.intent == []


def test_the_contact_flags_carry_their_intent() -> None:
    b = _builder()
    b.flag_dispute()
    b.flag_cease_contact()
    out = _build(b)
    assert out.dispute_flag and out.cease_contact_flag
    assert out.intent == [Intent.dispute, Intent.cease_contact]


def test_a_verified_call_is_always_a_right_party_call() -> None:
    out = _build(_builder(), contact_result=ContactResult.third_party)
    assert out.verified and out.contact_result is ContactResult.right_party


def test_a_promise_without_verification_is_dropped_so_the_outcome_still_validates() -> None:
    b = _builder()
    b.record_promise(PROMISE_DATE, Decimal("10.00"))
    out = _build(b, verified=False)
    assert out.promise_to_pay is None and Intent.promise_to_pay not in out.intent
    CallOutcome.model_validate(out.model_dump())


def test_the_contract_still_rejects_what_the_builder_does_not_repair() -> None:
    with pytest.raises(ValidationError):
        CallOutcome(
            call_id=str(uuid.uuid4()),
            direction=Direction.inbound,
            contact_result=ContactResult.third_party,
            verified=True,
            versions=VERSIONS,
        )


def test_a_callback_needs_an_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _builder().record_callback(dt.datetime(2026, 11, 13, 14, 30))


def test_transcript_uri_and_sentiment_are_carried() -> None:
    b = _builder()
    b.sentiment = Sentiment.negative
    out = _build(b, transcript_uri="file://var/transcripts/x.jsonl")
    assert out.transcript_uri == "file://var/transcripts/x.jsonl" and out.sentiment is Sentiment.negative


def test_turns_and_known_values_accumulate_for_the_transcript() -> None:
    b = _builder()
    b.know("Frank Alvarez", "", "Frank Alvarez")
    b.add_turn("caller", "hello", state="greeting")
    b.add_turn("agent", "hi", state="greeting")
    assert b.known_pii == ["Frank Alvarez"]
    assert [(t.speaker, t.state) for t in b.turns] == [("caller", "greeting"), ("agent", "greeting")]
    assert all(t.ts.tzinfo is not None for t in b.turns)


# --------------------------------------------------------------------------- summary template


def _summary(**kw: object) -> str:
    args: dict[str, object] = {
        "bank_name": BANK,
        "verified": True,
        "contact_result": ContactResult.right_party,
        "intents": [],
        "promise": None,
        "callback": None,
        "escalated": None,
        "dispute_flag": False,
        "cease_contact_flag": False,
    }
    args.update(kw)
    return compose_summary(**args)  # type: ignore[arg-type]


def test_formatters() -> None:
    assert format_money(Decimal("200.7")) == "$200.70"
    assert format_money(Decimal("1234.5")) == "$1,234.50"
    assert format_date(dt.date(2026, 11, 4)) == "4 November 2026"


def test_summary_for_a_promise_to_pay() -> None:
    text = _summary(
        intents=[Intent.promise_to_pay],
        promise=PromiseToPay(date=PROMISE_DATE, amount=Decimal("200.72")),
    )
    assert (
        text == "Thank you for speaking with Meridian Bank. You promised to pay $200.72 by 12 November 2026."
    )


def test_summary_for_a_callback() -> None:
    text = _summary(intents=[Intent.callback_request], callback=Callback(datetime=CALLBACK_AT))
    assert "We will call you back on 13 November 2026 at 14:30 UTC." in text


def test_summary_for_an_intent_to_pay_and_for_already_paid() -> None:
    assert "You told us you intend to pay." in _summary(intents=[Intent.intent_to_pay])
    assert "already paid" in _summary(intents=[Intent.already_paid])


def test_summary_for_an_escalation_and_for_a_callback_ticket() -> None:
    connected = _summary(escalated=Escalation(reason="hardship", target="200"))
    assert "connected you with a colleague" in connected
    ticket = _summary(escalated=Escalation(reason="hardship", target="callback_queue"))
    assert "A colleague will call you back." in ticket


def test_summary_for_a_dispute_and_a_cease_request() -> None:
    assert "noted your dispute" in _summary(dispute_flag=True, intents=[Intent.dispute])
    cease = _summary(cease_contact_flag=True, intents=[Intent.cease_contact])
    assert "stop phone calls" in cease


def test_summary_for_questions_and_the_plain_close() -> None:
    assert "your account" in _summary(intents=[Intent.account_question])
    assert "our policies" in _summary(intents=[Intent.policy_question])
    assert _summary() == "Thank you for speaking with Meridian Bank."


@pytest.mark.parametrize(
    ("contact_result", "expected"),
    [
        (ContactResult.third_party, "someone other than the account holder"),
        (ContactResult.wrong_number, "wrong number"),
        (ContactResult.no_answer, "could not reach"),
        (ContactResult.voicemail, "could not reach"),
        (ContactResult.busy, "could not reach"),
        (ContactResult.right_party, "could not verify"),
    ],
)
def test_summary_without_verification_shares_nothing(contact_result: ContactResult, expected: str) -> None:
    text = _summary(
        verified=False,
        contact_result=contact_result,
        promise=PromiseToPay(date=PROMISE_DATE, amount=Decimal("200.72")),
        callback=Callback(datetime=CALLBACK_AT),
    )
    assert expected in text
    assert figures_in(text) == set()
    assert "$" not in text


def test_the_summary_has_only_figures_from_the_outcome_fields() -> None:
    promise = PromiseToPay(date=PROMISE_DATE, amount=Decimal("200.72"))
    callback = Callback(datetime=CALLBACK_AT)
    text = _summary(
        intents=[Intent.promise_to_pay, Intent.callback_request],
        promise=promise,
        callback=callback,
        escalated=Escalation(reason="hardship", target="200"),
    )
    assert figures_in(text) == {"200.72", "12", "2026", "13", "14:30"}
    assert ungrounded_figures(text, promise=promise, callback=callback) == set()
    # the escalation target (an extension) never reaches the summary
    assert "200 " not in text and not text.endswith(" 200")


def test_a_figure_that_is_not_in_the_outcome_is_reported() -> None:
    promise = PromiseToPay(date=PROMISE_DATE, amount=Decimal("200.72"))
    text = "You promised to pay $250.00 by 12 November 2026."
    assert ungrounded_figures(text, promise=promise, callback=None) == {"250.00"}
    assert ungrounded_figures("You owe $5.", promise=None, callback=None) == {"5"}


def test_the_readback_repeats_only_commitments() -> None:
    promise = PromiseToPay(date=PROMISE_DATE, amount=Decimal("200.72"))
    callback = Callback(datetime=CALLBACK_AT)
    assert readback_line(promise=promise, callback=None) == (
        "To confirm: you will pay $200.72 by 12 November 2026."
    )
    assert readback_line(promise=None, callback=callback) == (
        "To confirm: we will call you back on 13 November 2026 at 14:30 UTC."
    )
    both = readback_line(promise=promise, callback=callback)
    assert both.startswith("To confirm: you will pay $200.72") and both.endswith("at 14:30 UTC.")
    assert readback_line(promise=None, callback=None) is None
