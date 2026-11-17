"""T-AGENT-08 · AGENT-R11 / AGENT-R12: `finalize()` writes exactly one validated outcome per call.

Completed, third-party, locked and disconnected calls each end with one `record_call_outcome`; a gateway
error is retried once and then logged without PII; the transcript is written redacted.
"""

import asyncio
import datetime as dt
import json
import logging
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from supportagent.agent.context import CallState
from supportagent.agent.finalize import (
    attach_recorder,
    finalize,
    llm_sentiment_classifier,
    parse_sentiment,
)
from supportagent.agent.outcome import LatencyFigures
from supportagent.agent.testing import FakeGateway, ScriptedLLM, say
from supportagent.common.settings import Settings
from supportagent.contracts.call import CallMetadata
from supportagent.contracts.gateway import OutcomeRecordedOutput, ToolResult
from supportagent.contracts.outcome import (
    CallOutcome,
    Campaign,
    ContactResult,
    Direction,
    Sentiment,
    StageLatency,
    Versions,
)
from supportagent.observability.transcripts import TranscriptStore

VERSIONS = Versions(prompt="0.1.0+abc", policy="p-1", model="llm:llamacpp/test")
PROMISE_DATE = dt.date(2026, 11, 12)
NAME = "Frank Alvarez"


def _state(
    gateway: FakeGateway | None = None,
    direction: Direction = Direction.outbound,
    campaign: Campaign = Campaign.missed_payment,
) -> CallState:
    meta = CallMetadata(
        call_id=str(uuid.uuid4()), direction=direction, campaign=campaign, customer_id=9001, loan_id=1
    )
    return CallState(
        metadata=meta, gateway=gateway or FakeGateway(), session_token=SecretStr("fake-session-token")
    )


def _fake(state: CallState) -> FakeGateway:
    gw = state.gateway  # CallState wraps the gateway; the fake is behind the guard
    return gw.inner  # type: ignore[attr-defined, no-any-return]


@pytest.fixture
def store(tmp_path: Path) -> TranscriptStore:
    return TranscriptStore(tmp_path)


async def _run(state: CallState, settings: Settings, store: TranscriptStore, **kw: Any):  # type: ignore[no-untyped-def]
    return await finalize(state, settings=settings, store=store, versions=VERSIONS, retry_delay_s=0, **kw)


# --------------------------------------------------------------------------- the four call types


async def test_a_completed_verified_call_writes_one_validated_outcome(
    settings: Settings, store: TranscriptStore
) -> None:
    state = _state()
    state.verified, state.contact_result = True, ContactResult.right_party
    state.disclosures_given = ["ai_recording@1.0", "collections@1.0"]
    state.first_name = "Frank"
    b = state.outcome
    b.know(NAME)
    b.record_promise(PROMISE_DATE, Decimal("200.72"))
    b.add_kb_source("late-fees@1.0#Late fees")
    b.set_latency(LatencyFigures(900, 1500, {"e2e": StageLatency(p50_ms=900, p90_ms=1500)}))
    b.add_turn("agent", "Hello, this is Ava.", state="greeting")
    b.add_turn("caller", f"This is {NAME}.", state="verify")

    result = await _run(state, settings, store)

    gw = _fake(state)
    assert result is not None and result.recorded and result.attempts == 1
    assert gw.tools_called.count("record_call_outcome") == 1
    out = gw.outcomes[0]
    CallOutcome.model_validate(out.model_dump())
    assert out.versions == VERSIONS and out.verified and out.contact_result is ContactResult.right_party
    assert out.promise_to_pay is not None and out.promise_to_pay.amount == Decimal("200.72")
    assert out.disclosures_given == ["ai_recording@1.0", "collections@1.0"]
    assert out.kb_sources == ["late-fees@1.0#Late fees"] and out.latency_p90 == 1500
    assert out.summary == (
        "Thank you for speaking with Meridian Bank. You promised to pay $200.72 by 12 November 2026."
    )
    assert out.transcript_uri == store.uri(state.call_id)


async def test_the_transcript_is_written_redacted_with_a_closing_marker(
    settings: Settings, store: TranscriptStore
) -> None:
    state = _state()
    state.verified, state.contact_result = True, ContactResult.right_party
    state.outcome.know(NAME)
    state.outcome.add_turn("caller", f"My name is {NAME} and my number is 555 010 0001.", state="verify")
    await _run(state, settings, store)

    turns = store.read(state.call_id)
    assert [t.seq for t in turns] == [1, 2]
    assert NAME not in turns[0].text and "Frank" not in turns[0].text and "0001" not in turns[0].text
    assert turns[1].speaker == "system" and turns[1].text.startswith("call ended")
    assert NAME not in (store.base_dir / f"{state.call_id}.jsonl").read_text()


async def test_a_third_party_call_is_recorded_without_account_details(
    settings: Settings, store: TranscriptStore
) -> None:
    state = _state()
    state.contact_result = ContactResult.third_party
    state.disclosures_given = ["ai_recording@1.0", "third_party_neutral@1.0"]
    state.third_party_callback_note = "after six"
    await _run(state, settings, store)

    out = _fake(state).outcomes[0]
    assert out.contact_result is ContactResult.third_party and not out.verified
    assert "someone other than the account holder" in out.summary and "$" not in out.summary
    assert out.promise_to_pay is None and out.customer_id == 9001


async def test_a_locked_call_is_a_right_party_call_that_was_not_verified(
    settings: Settings, store: TranscriptStore
) -> None:
    state = _state()
    state.locked = True  # two failed verifications; the contact result was never set explicitly
    state.outcome.record_escalation("verification_failed", "callback_queue")
    await _run(state, settings, store)

    out = _fake(state).outcomes[0]
    assert out.contact_result is ContactResult.right_party and not out.verified
    assert out.escalated is not None and out.escalated.reason == "verification_failed"
    assert "could not verify" in out.summary


async def test_a_disconnect_mid_flow_writes_one_outcome_with_what_was_gathered(
    settings: Settings, store: TranscriptStore
) -> None:
    state = _state()
    state.disclosures_given = ["ai_recording@1.0"]
    state.states_visited = ["greeting", "verify"]
    state.outcome.add_turn("agent", "May I speak with Frank?", state="greeting")

    result = await _run(state, settings, store)

    gw = _fake(state)
    assert gw.tools_called == ["record_call_outcome"]
    out = gw.outcomes[0]
    assert result is not None and result.recorded
    assert out.disclosures_given == ["ai_recording@1.0"] and not out.verified
    assert out.contact_result is ContactResult.no_answer  # nobody was confirmed on the line
    assert store.read(state.call_id)[-1].text == "call ended after: greeting, verify"


async def test_an_inbound_caller_who_spoke_but_never_verified_is_a_right_party_call(
    settings: Settings, store: TranscriptStore
) -> None:
    state = _state(direction=Direction.inbound, campaign=Campaign.inbound)
    state.outcome.add_turn("caller", "hi", state="greeting")
    await _run(state, settings, store)
    out = _fake(state).outcomes[0]
    assert out.contact_result is ContactResult.right_party and not out.verified


async def test_the_outcome_is_written_once_however_often_finalize_runs(
    settings: Settings, store: TranscriptStore
) -> None:
    state = _state()
    first = await _run(state, settings, store)
    second = await _run(state, settings, store)
    both = await asyncio.gather(_run(_state(), settings, store), _run(state, settings, store))
    assert first is not None and second is None and both[1] is None
    assert _fake(state).tools_called.count("record_call_outcome") == 1


async def test_versions_come_from_the_settings_when_not_given(
    settings: Settings, store: TranscriptStore
) -> None:
    state = _state()
    await finalize(state, settings=settings, store=store, retry_delay_s=0)
    versions = _fake(state).outcomes[0].versions
    assert versions.prompt and versions.policy and versions.model.startswith("llm:llamacpp/")


# --------------------------------------------------------------------------- gateway errors


class FlakyGateway(FakeGateway):
    """`record_call_outcome` fails `failures` times (an upstream error) before it succeeds."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.attempts = 0

    async def record_call_outcome(self, outcome: CallOutcome) -> ToolResult[OutcomeRecordedOutput]:
        self.attempts += 1
        if self.attempts <= self.failures:
            return ToolResult.failure("upstream_error", "gateway unavailable")
        return await super().record_call_outcome(outcome)


async def test_a_gateway_error_is_retried_once(settings: Settings, store: TranscriptStore) -> None:
    gw = FlakyGateway(failures=1)
    state = _state(gw)
    result = await _run(state, settings, store)
    assert result is not None and result.recorded and result.attempts == 2
    assert gw.attempts == 2 and len(gw.outcomes) == 1


async def test_a_second_failure_is_logged_without_pii_and_not_retried_again(
    settings: Settings, store: TranscriptStore, caplog: pytest.LogCaptureFixture
) -> None:
    gw = FlakyGateway(failures=5)
    state = _state(gw)
    state.verified, state.contact_result, state.first_name = True, ContactResult.right_party, "Frank"
    state.outcome.know(NAME)
    state.outcome.record_promise(PROMISE_DATE, Decimal("200.72"))
    state.outcome.add_turn("caller", f"I am {NAME}", state="verify")
    with caplog.at_level(logging.DEBUG):
        result = await _run(state, settings, store)

    assert result is not None and not result.recorded and result.attempts == 2
    assert gw.attempts == 2 and gw.outcomes == []
    failures = [r for r in caplog.records if "outcome not recorded" in r.getMessage()]
    assert len(failures) == 1
    dumped = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for secret in (NAME, "Frank", "200.72", "fake-session-token", "promised"):
        assert secret not in dumped
    assert getattr(failures[0], "code", None) == "upstream_error"


async def test_an_exception_from_the_gateway_is_handled_like_an_error(
    settings: Settings, store: TranscriptStore
) -> None:
    class Boom(FakeGateway):
        async def record_call_outcome(self, outcome: CallOutcome) -> ToolResult[OutcomeRecordedOutput]:
            raise ConnectionError("secret-token-123 refused")

    state = _state(Boom())
    result = await _run(state, settings, store)
    assert result is not None and not result.recorded and result.attempts == 2


async def test_a_broken_transcript_store_does_not_lose_the_outcome(
    settings: Settings, tmp_path: Path
) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    state = _state()
    state.outcome.add_turn("caller", "hello", state="greeting")
    await _run(state, settings, TranscriptStore(blocker / "sub"))
    out = _fake(state).outcomes[0]
    assert out.transcript_uri is None and len(_fake(state).outcomes) == 1


# --------------------------------------------------------------------------- sentiment


def test_parse_sentiment_maps_the_label_and_falls_back_to_unknown() -> None:
    assert parse_sentiment("Negative.") is Sentiment.negative
    assert parse_sentiment("  positive\n") is Sentiment.positive
    assert parse_sentiment("neutral overall") is Sentiment.neutral
    assert parse_sentiment("angry") is Sentiment.unknown
    assert parse_sentiment("") is Sentiment.unknown


async def test_the_sentiment_comes_from_the_classifier_over_the_redacted_transcript(
    settings: Settings, store: TranscriptStore
) -> None:
    seen: list[str] = []

    async def classify(text: str) -> str:
        seen.append(text)
        return "negative"

    state = _state()
    state.outcome.know(NAME)
    state.outcome.add_turn("caller", f"{NAME} is not happy with these fees", state="verify")
    await _run(state, settings, store, classify=classify)

    assert _fake(state).outcomes[0].sentiment is Sentiment.negative
    assert len(seen) == 1 and seen[0].startswith("caller:") and NAME not in seen[0]


@pytest.mark.parametrize("mode", ["raises", "garbage", "slow"])
async def test_a_failing_classifier_leaves_sentiment_unknown(
    settings: Settings, store: TranscriptStore, mode: str
) -> None:
    async def classify(text: str) -> str:
        if mode == "raises":
            raise RuntimeError("model down")
        if mode == "slow":
            await asyncio.sleep(5)
        return "furious"

    state = _state()
    state.outcome.add_turn("caller", "hello", state="greeting")
    await _run(state, settings, store, classify=classify, sentiment_timeout_s=0.05)
    assert _fake(state).outcomes[0].sentiment is Sentiment.unknown


async def test_no_caller_speech_means_the_classifier_is_not_asked(
    settings: Settings, store: TranscriptStore
) -> None:
    async def classify(text: str) -> str:
        raise AssertionError("not asked")

    state = _state()
    state.outcome.add_turn("agent", "May I speak with Frank?", state="greeting")
    await _run(state, settings, store, classify=classify)
    assert _fake(state).outcomes[0].sentiment is Sentiment.unknown


async def test_the_llm_classifier_uses_the_model_and_returns_its_word() -> None:
    llm = ScriptedLLM([say("neutral")])
    classify = llm_sentiment_classifier(llm)
    assert parse_sentiment(await classify("caller: ok thanks")) is Sentiment.neutral
    assert llm.remaining == 0


# --------------------------------------------------------------------------- the session recorder


class _FakeSession:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def on(self, event: str, handler: Any = None) -> Any:
        self.handlers[event] = handler
        return handler


def _item(**kw: Any) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(**kw)


def test_the_recorder_collects_speech_and_tool_calls_in_order() -> None:
    state = _state()
    state.states_visited = ["greeting"]
    session = _FakeSession()
    attach_recorder(session, state)  # type: ignore[arg-type]
    handle = session.handlers["conversation_item_added"]
    handle(_item(item=_item(type="message", role="assistant", text_content="Hello.")))
    handle(_item(item=_item(type="message", role="user", text_content="Hi, it is me.")))
    handle(_item(item=_item(type="message", role="user", text_content="")))  # empty: ignored
    handle(_item(item=_item(type="message", role="system", text_content="prompt")))  # not a turn
    handle(
        _item(
            item=_item(
                type="function_call", name="verify_identity", arguments=json.dumps({"loan_last4": "4417"})
            )
        )
    )
    turns = state.outcome.turns
    assert [(t.speaker, t.text) for t in turns[:2]] == [("agent", "Hello."), ("caller", "Hi, it is me.")]
    assert turns[2].tool is not None and turns[2].tool.name == "verify_identity"
    assert turns[2].tool.args == {"loan_last4": "4417"} and turns[2].speaker == "system"
    assert all(t.state == "greeting" for t in turns)
