"""End of call: write the redacted transcript and exactly one validated outcome (AGENT-R11, AGENT-R12).

`attach_recorder(session, state)` listens to the session's `conversation_item_added` events and keeps the
turns (caller and agent speech, tool calls) on `state.outcome`. `finalize(state, ...)` runs from the worker's
shutdown callback, whether the call ended normally, was dropped by the caller or was cut by an error:

1. the turns go through `TranscriptStore.append` (the only way to write, always redacted) plus a final
   "call ended" marker, which also makes `transcript_uri` valid for a call with no speech;
2. sentiment is classified once over the **redacted** transcript (`unknown` if there is no classifier, no
   caller speech, a timeout or an unusable answer; it never affects the flow);
3. the post-call summary is composed from the outcome fields (a template, `summary.py`), checked for
   figures that are not in the outcome, and redacted;
4. the outcome is built (versions, latency, transcript URI) and written with `record_call_outcome`. A failed
   write is retried once; a second failure is logged with the error code only (no outcome, no PII).

It writes at most once per call: a second call is a no-op. Which `contact_result` a call gets when no state
set one: verified or locked → `right_party`; otherwise `no_answer` if nobody spoke, `right_party` for an
inbound caller who spoke, and `no_answer` for an outbound call dropped before the right-party check.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from livekit.agents.llm import LLM, ChatContext

from supportagent.agent.context import CallState
from supportagent.agent.summary import compose_summary, ungrounded_figures
from supportagent.common.logging import get_logger
from supportagent.common.settings import Settings
from supportagent.contracts.outcome import CallOutcome, ContactResult, Direction, Intent, Sentiment, Versions
from supportagent.contracts.transcript import ToolRef
from supportagent.observability import redaction
from supportagent.observability.transcripts import TranscriptStore
from supportagent.observability.versions import compute_versions

log = get_logger(__name__)

RETRY_DELAY_S = 0.5
SENTIMENT_TIMEOUT_S = 10.0
OUTCOME_ATTEMPTS = 2  # one try and one retry

SentimentClassifier = Callable[[str], Awaitable[str]]
"""Takes the redacted transcript text, returns a word: positive, neutral or negative."""

_FALLBACK_SUMMARY = "Thank you for speaking with the bank."


@dataclass(frozen=True)
class FinalizeResult:
    outcome: CallOutcome
    recorded: bool  # `record_call_outcome` succeeded
    attempts: int
    transcript_uri: str | None


# --------------------------------------------------------------------------- recording the session


def attach_recorder(session: Any, state: CallState) -> None:
    """Keep every spoken turn and tool call of `session` on `state.outcome` (raw; redacted at finalize)."""

    def on_item(event: Any) -> None:
        item = event.item
        kind = getattr(item, "type", None)
        current = state.states_visited[-1] if state.states_visited else None
        if kind == "message":
            role = getattr(item, "role", None)
            text = (getattr(item, "text_content", None) or "").strip()
            if text and role in ("user", "assistant"):
                state.outcome.add_turn("caller" if role == "user" else "agent", text, state=current)
        elif kind == "function_call":
            try:
                args = json.loads(getattr(item, "arguments", None) or "{}")
            except json.JSONDecodeError:
                args = {}
            tool = ToolRef(name=item.name, args=args if isinstance(args, dict) else {}, decision="allowed")
            state.outcome.add_turn("system", f"tool call: {item.name}", state=current, tool=tool)

    session.on("conversation_item_added", on_item)


# --------------------------------------------------------------------------- sentiment

_SENTIMENT_PROMPT = (
    "You classify the overall sentiment of the caller in a loan-servicing phone call. Answer with exactly "
    "one word: positive, neutral or negative."
)


def parse_sentiment(answer: str) -> Sentiment:
    """The first word of `answer` as a `Sentiment` (`unknown` is never produced by a model's answer)."""
    words = answer.strip().lower().split()
    word = words[0].strip(".,!?:;\"'") if words else ""
    if word in ("positive", "neutral", "negative"):
        return Sentiment(word)
    return Sentiment.unknown


def llm_sentiment_classifier(llm: LLM) -> SentimentClassifier:
    """A `SentimentClassifier` that asks `llm` (the call's own model) for one word."""

    async def classify(transcript: str) -> str:
        ctx = ChatContext()
        ctx.add_message(role="system", content=_SENTIMENT_PROMPT)
        ctx.add_message(role="user", content=transcript)
        parts: list[str] = []
        async with llm.chat(chat_ctx=ctx) as stream:
            async for chunk in stream:
                if chunk.delta is not None and chunk.delta.content:
                    parts.append(chunk.delta.content)
        return "".join(parts)

    return classify


async def _classify(
    classify: SentimentClassifier | None, store: TranscriptStore, call_id: str, timeout_s: float
) -> Sentiment:
    if classify is None:
        return Sentiment.unknown
    try:
        turns = store.read(call_id)
    except Exception:
        return Sentiment.unknown
    if not any(t.speaker == "caller" for t in turns):
        return Sentiment.unknown
    text = "\n".join(f"{t.speaker}: {t.text}" for t in turns if t.speaker in ("caller", "agent"))
    try:
        async with asyncio.timeout(timeout_s):
            return parse_sentiment(await classify(text))
    except Exception as exc:  # a failed or slow classifier must never lose the outcome
        log.warning("sentiment not classified", extra={"error": type(exc).__name__})
        return Sentiment.unknown


# --------------------------------------------------------------------------- finalize


def _contact_result(state: CallState) -> ContactResult:
    if state.contact_result is not None:
        return state.contact_result
    if state.verified or state.locked:
        return ContactResult.right_party
    spoke = any(t.speaker == "caller" for t in state.outcome.turns)
    if spoke and state.metadata.direction is Direction.inbound:
        return ContactResult.right_party
    return ContactResult.no_answer


def _write_transcript(state: CallState, store: TranscriptStore) -> str | None:
    """Append the turns and the closing marker through the redacting store. `None` if it cannot be written."""
    builder = state.outcome
    known = list(
        dict.fromkeys([*builder.known_pii, *(n for n in (state.first_name, state.callee_first_name) if n)])
    )
    visited = ", ".join(state.states_visited) or "no state"
    try:
        for turn in builder.turns:
            store.append(state.call_id, turn.model_copy(update={"known_pii": known}))
        marker = f"call ended after: {visited}"
        if state.end_reason:
            marker += f"; reason: {state.end_reason}"
        builder.add_turn("system", marker, state=None)
        store.append(state.call_id, builder.turns[-1])
        return store.uri(state.call_id)
    except Exception as exc:  # disk full, bad directory, ...: the outcome must still be written
        log.error("transcript not written", extra={"error": type(exc).__name__})
        return None


def _summary(state: CallState, bank_name: str, contact_result: ContactResult, verified: bool) -> str:
    b = state.outcome
    intents = [i for i in b.intents if verified or i is not Intent.promise_to_pay]
    promise = b.promise if verified else None
    text = compose_summary(
        bank_name=bank_name,
        verified=verified,
        contact_result=contact_result,
        intents=intents,
        promise=promise,
        callback=b.callback,
        escalated=b.escalated,
        dispute_flag=b.dispute_flag,
        cease_contact_flag=b.cease_contact_flag,
    )
    if ungrounded_figures(text, promise=promise, callback=b.callback):
        log.error("summary had figures outside the outcome; using the plain line")
        return _FALLBACK_SUMMARY
    known = [*b.known_pii, *(n for n in (state.first_name, state.callee_first_name) if n)]
    return redaction.redact(text, known=tuple(known)).text


async def finalize(
    state: CallState,
    *,
    settings: Settings,
    store: TranscriptStore | None = None,
    versions: Versions | None = None,
    classify: SentimentClassifier | None = None,
    retry_delay_s: float = RETRY_DELAY_S,
    sentiment_timeout_s: float = SENTIMENT_TIMEOUT_S,
) -> FinalizeResult | None:
    """Write the call's transcript and its one outcome. Returns `None` if this call was already finalized."""
    builder = state.outcome
    if builder.finalized:
        return None
    builder.finalized = True  # before any await: concurrent shutdown paths cannot both write

    store = store or TranscriptStore()
    verified = state.verified
    contact_result = _contact_result(state)
    uri = _write_transcript(state, store)
    builder.sentiment = await _classify(classify, store, state.call_id, sentiment_timeout_s)
    bank_name = settings.persona.bank_name
    outcome = builder.build(
        verified=verified,
        contact_result=contact_result,
        disclosures_given=state.disclosures_given,
        versions=versions or compute_versions(settings),
        summary=_summary(state, bank_name, contact_result, verified),
        bank_name=bank_name,
        transcript_uri=uri,
    )

    attempts = 0
    for attempt in range(1, OUTCOME_ATTEMPTS + 1):
        attempts = attempt
        code = "exception"
        try:
            result = await state.gateway.record_call_outcome(outcome)
            if result.ok:
                return FinalizeResult(outcome, True, attempts, uri)
            code = result.error.code if result.error is not None else "unknown"
        except Exception as exc:  # the message may carry secrets: log the type only
            code = type(exc).__name__
        log.warning("outcome write failed", extra={"attempt": attempt, "code": code})
        if attempt < OUTCOME_ATTEMPTS:
            await asyncio.sleep(retry_delay_s)
    log.error("outcome not recorded", extra={"attempts": attempts, "code": code})
    return FinalizeResult(outcome, False, attempts, uri)
