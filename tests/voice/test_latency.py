"""T-VOICE-04 · VOICE-R6: per-turn latency from LiveKit `metrics_collected`, reported as p50/p90.

Synthetic metric objects only: no session, no network. A turn is complete when its EOU, LLM and TTS metrics
(all keyed by `speech_id`) have arrived, in any order.
"""

import logging
from typing import Any

import pytest
from livekit.agents import metrics as lk

from supportagent.contracts.outcome import (
    CallOutcome,
    Campaign,
    ContactResult,
    Direction,
    StageLatency,
    Versions,
)
from supportagent.voice.latency import (
    LatencySummary,
    LatencyTracker,
    attach_latency_tracker,
    percentile,
)


def eou(speech_id: str, delay: float, transcription: float = 0.2) -> lk.EOUMetrics:
    return lk.EOUMetrics(
        timestamp=1.0,
        end_of_utterance_delay=delay,
        transcription_delay=transcription,
        on_user_turn_completed_delay=0.0,
        speech_id=speech_id,
    )


def llm(speech_id: str | None, ttft: float, *, cancelled: bool = False) -> lk.LLMMetrics:
    return lk.LLMMetrics(
        label="llm",
        request_id="r",
        timestamp=1.0,
        duration=ttft + 0.5,
        ttft=ttft,
        cancelled=cancelled,
        completion_tokens=10,
        prompt_tokens=100,
        prompt_cached_tokens=0,
        total_tokens=110,
        tokens_per_second=20.0,
        speech_id=speech_id,
    )


def tts(speech_id: str | None, ttfb: float, *, cancelled: bool = False) -> lk.TTSMetrics:
    return lk.TTSMetrics(
        label="tts",
        request_id="r",
        timestamp=1.0,
        ttfb=ttfb,
        duration=ttfb + 0.3,
        audio_duration=1.0,
        cancelled=cancelled,
        characters_count=40,
        streamed=True,
        speech_id=speech_id,
    )


def feed_turn(tracker: LatencyTracker, speech_id: str, delay: float, ttft: float, ttfb: float) -> None:
    for m in (eou(speech_id, delay), llm(speech_id, ttft), tts(speech_id, ttfb)):
        tracker.add(m)


# ------------------------------------------------------------------ per-turn maths


def test_a_complete_turn_sums_eou_ttft_and_ttfb() -> None:
    tracker = LatencyTracker()
    feed_turn(tracker, "s1", 0.4, 0.5, 0.3)
    [turn] = tracker.turns()
    assert turn.speech_id == "s1"
    assert turn.stages["eou"] == pytest.approx(0.4)
    assert turn.stages["stt"] == pytest.approx(0.2)  # the EOU metric's transcription_delay
    assert turn.stages["llm_ttft"] == pytest.approx(0.5)
    assert turn.stages["tts_ttfb"] == pytest.approx(0.3)
    assert turn.e2e == pytest.approx(1.2)  # eou + llm_ttft + tts_ttfb


def test_events_arrive_out_of_order_and_interleaved_but_match_by_speech_id() -> None:
    tracker = LatencyTracker()
    for m in (
        tts("b", 0.2),
        llm("a", 0.6),
        eou("b", 0.5),
        tts("a", 0.4),
        llm("b", 0.3),
        eou("a", 0.3),
    ):
        tracker.add(m)
    turns = {t.speech_id: t for t in tracker.turns()}
    assert turns["a"].e2e == pytest.approx(0.3 + 0.6 + 0.4)
    assert turns["b"].e2e == pytest.approx(0.5 + 0.3 + 0.2)


def test_turns_with_missing_stages_are_skipped_not_counted_as_zero() -> None:
    tracker = LatencyTracker()
    feed_turn(tracker, "spoken", 0.4, 0.5, 0.3)
    tracker.add(eou("tool-only", 0.4))  # a tool-only turn: the model ran but nothing was spoken
    tracker.add(llm("tool-only", 0.5))
    tracker.add(eou("no-llm", 0.4))
    tracker.add(tts("no-llm", 0.3))
    assert [t.speech_id for t in tracker.turns()] == ["spoken"]
    summary = tracker.summary()
    assert summary.turns == 1
    assert summary.e2e.p50 == pytest.approx(1.2)  # not dragged down by zeros


def test_metrics_without_a_speech_id_and_other_types_are_ignored() -> None:
    tracker = LatencyTracker()
    tracker.add(llm(None, 0.5))
    tracker.add(tts(None, 0.3))
    tracker.add(
        lk.STTMetrics(
            label="stt",
            request_id="r",
            timestamp=1.0,
            duration=0.3,
            audio_duration=1.0,
            streamed=False,
        )
    )
    assert tracker.turns() == []


def test_cancelled_model_and_speech_calls_do_not_complete_a_turn() -> None:
    tracker = LatencyTracker()
    tracker.add(eou("x", 0.4))
    tracker.add(llm("x", 0.5, cancelled=True))
    tracker.add(tts("x", 0.3, cancelled=True))  # the caller barged in before any audio
    assert tracker.turns() == []


def test_a_tool_turn_counts_every_model_call_before_the_reply() -> None:
    tracker = LatencyTracker()
    tracker.add(eou("t", 0.4))
    tracker.add(llm("t", 0.5))  # asks for a tool
    tracker.add(llm("t", 0.3))  # then writes the reply
    tracker.add(tts("t", 0.2))
    [turn] = tracker.turns()
    assert turn.stages["llm_ttft"] == pytest.approx(0.8)
    assert turn.e2e == pytest.approx(1.4)


def test_the_first_tts_segment_sets_the_time_to_first_audio() -> None:
    tracker = LatencyTracker()
    tracker.add(eou("t", 0.4))
    tracker.add(llm("t", 0.5))
    tracker.add(tts("t", 0.2))  # first sentence
    tracker.add(tts("t", 0.9))  # later sentences do not delay the first audio
    [turn] = tracker.turns()
    assert turn.stages["tts_ttfb"] == pytest.approx(0.2)


# ------------------------------------------------------------------ percentiles


def test_percentile_for_one_two_and_ten_values() -> None:
    assert percentile([0.7], 50) == pytest.approx(0.7)
    assert percentile([0.7], 90) == pytest.approx(0.7)
    assert percentile([1.0, 2.0], 50) == pytest.approx(1.5)
    assert percentile([1.0, 2.0], 90) == pytest.approx(1.9)
    ten = [float(i) for i in range(1, 11)]
    assert percentile(ten, 50) == pytest.approx(5.5)
    assert percentile(ten, 90) == pytest.approx(9.1)
    assert percentile(list(reversed(ten)), 90) == pytest.approx(9.1)  # order does not matter


def test_summary_percentiles_over_ten_turns() -> None:
    tracker = LatencyTracker()
    for i in range(1, 11):
        feed_turn(tracker, f"s{i}", 0.1 * i, 0.0, 0.0)
    summary = tracker.summary()
    assert summary.turns == 10
    assert summary.stages["eou"].p50 == pytest.approx(0.55)
    assert summary.stages["eou"].p90 == pytest.approx(0.91)
    assert summary.e2e.p50 == pytest.approx(0.55)


def test_an_empty_tracker_returns_none_values_not_an_error() -> None:
    summary = LatencyTracker().summary()
    assert summary.turns == 0
    assert summary.e2e.p50 is None and summary.e2e.p90 is None
    assert all(s.p50 is None and s.p90 is None for s in summary.stages.values())
    assert summary.p50_ms is None and summary.p90_ms is None
    assert summary.to_outcome() == {"latency_p50": None, "latency_p90": None, "latency_stages": {}}
    assert summary.stage_latencies() == {}


# ------------------------------------------------------------------ outcome


def _outcome(**latency: Any) -> CallOutcome:
    return CallOutcome(
        call_id="3f6b3c1e-9d3a-4c1a-8f0e-2b6a5f1d7c11",
        direction=Direction.inbound,
        campaign=Campaign.playground,
        contact_result=ContactResult.right_party,
        versions=Versions(prompt="p", policy="q", model="m"),
        **latency,
    )


def test_to_outcome_validates_against_the_call_outcome_model() -> None:
    tracker = LatencyTracker()
    feed_turn(tracker, "s1", 0.4, 0.5, 0.3)
    feed_turn(tracker, "s2", 0.6, 0.7, 0.4)
    data = tracker.summary().to_outcome()
    outcome = _outcome(**data)
    assert outcome.latency_p50 == 1450  # ms: end of speech → first agent audio, p50 of (1.2, 1.7)
    assert outcome.latency_p90 == 1650
    assert set(outcome.latency_stages) == {"eou", "stt", "llm_ttft", "tts_ttfb", "e2e"}
    assert outcome.latency_stages["e2e"] == StageLatency(p50_ms=1450, p90_ms=1650)
    assert outcome.latency_stages["llm_ttft"].p50_ms == 600


def test_stage_latencies_gives_the_contract_type_for_the_outcome_builder() -> None:
    tracker = LatencyTracker()
    feed_turn(tracker, "s1", 0.4, 0.5, 0.3)
    summary = tracker.summary()
    stages = summary.stage_latencies()
    assert stages["e2e"] == StageLatency(p50_ms=1200, p90_ms=1200)
    assert (summary.p50_ms, summary.p90_ms) == (1200, 1200)


def test_milliseconds_are_rounded_to_whole_numbers() -> None:
    tracker = LatencyTracker()
    feed_turn(tracker, "s1", 0.4004, 0.5004, 0.3004)
    assert tracker.summary().p50_ms == 1201


# ------------------------------------------------------------------ logging


def test_one_structured_log_line_per_complete_turn_without_text(caplog: pytest.LogCaptureFixture) -> None:
    tracker = LatencyTracker()
    with caplog.at_level(logging.INFO):
        tracker.add(eou("s1", 0.4))
        tracker.add(llm("s1", 0.5))
        assert [r for r in caplog.records if r.getMessage() == "turn latency"] == []  # not complete yet
        tracker.add(tts("s1", 0.3))
        tracker.add(tts("s1", 0.9))  # a later segment of the same turn does not log again
    lines = [r for r in caplog.records if r.getMessage() == "turn latency"]
    assert len(lines) == 1
    record = lines[0].__dict__
    assert record["speech_id"] == "s1"
    assert record["e2e_ms"] == 1200
    assert record["eou_ms"] == 400 and record["llm_ttft_ms"] == 500 and record["tts_ttfb_ms"] == 300
    assert record["stt_ms"] == 200


def test_a_turn_is_counted_once_even_if_late_metrics_arrive() -> None:
    tracker = LatencyTracker()
    feed_turn(tracker, "s1", 0.4, 0.5, 0.3)
    tracker.add(llm("s1", 9.0))
    tracker.add(eou("s1", 9.0))
    assert len(tracker.turns()) == 1
    assert tracker.turns()[0].e2e == pytest.approx(1.2)


# ------------------------------------------------------------------ session hook


class FakeSession:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}

    def on(self, event: str, callback: Any = None) -> Any:
        self.handlers.setdefault(event, []).append(callback)
        return callback

    def emit(self, event: str, payload: object) -> None:
        for handler in self.handlers.get(event, []):
            handler(payload)


class Event:
    def __init__(self, metrics: object) -> None:
        self.metrics = metrics


def test_attach_latency_tracker_feeds_metrics_collected_events() -> None:
    session = FakeSession()
    tracker = LatencyTracker()
    attach_latency_tracker(session, tracker)  # type: ignore[arg-type]
    assert list(session.handlers) == ["metrics_collected"]
    for m in (eou("s1", 0.4), llm("s1", 0.5), tts("s1", 0.3)):
        session.emit("metrics_collected", Event(m))
    assert tracker.summary().p50_ms == 1200


def test_summary_type_is_exported() -> None:
    assert isinstance(LatencyTracker().summary(), LatencySummary)
