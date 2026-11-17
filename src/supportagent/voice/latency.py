"""Per-turn latency from LiveKit `metrics_collected` events, reported as p50/p90 (VOICE-R6).

A turn is keyed by the `speech_id` that LiveKit puts on its EOU, LLM and TTS metrics (STT metrics carry none,
so the STT stage is the EOU metric's `transcription_delay`: end of speech → final transcript). A turn is
complete once its three metrics have arrived, in any order:

    eou       end-of-utterance delay (the caller stopped → the turn is judged finished)
    stt       transcription delay (reported with the EOU metric)
    llm_ttft  time to first token, summed over the turn's model calls: a turn where the model calls a tool
              before speaking waits for every call, so each counts (tool execution time is not included)
    tts_ttfb  time to first audio byte of the turn's first spoken segment
    e2e       eou + llm_ttft + tts_ttfb: end of speech → first agent audio

Turns that never complete (a tool-only turn speaks nothing; a barge-in before any audio cancels the
speech) are skipped, never counted as zero. Cancelled model and speech calls do not count. Each completed turn
logs one structured line with the figures only (no transcript text, so no PII).

Usage (the worker wires it in, T-AGENT-13 / T-VOICE-05)::

    tracker = LatencyTracker()
    attach_latency_tracker(session, tracker)
    ...
    summary = tracker.summary()
    state.outcome.set_latency(LatencyFigures(summary.p50_ms, summary.p90_ms, summary.stage_latencies()))
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from livekit.agents import AgentSession, metrics
from livekit.agents.voice.events import MetricsCollectedEvent

from supportagent.common.logging import get_logger
from supportagent.contracts.outcome import StageLatency

log = get_logger(__name__)

STAGES: tuple[str, ...] = ("eou", "stt", "llm_ttft", "tts_ttfb", "e2e")
MAX_PENDING_TURNS = 256  # incomplete turns kept while waiting for their other metrics


def percentile(values: Sequence[float], p: int) -> float:
    """The p-th percentile (1-99) by linear interpolation between closest ranks ("inclusive")."""
    if not values:
        raise ValueError("percentile of no values")
    if len(values) == 1:
        return float(values[0])
    return statistics.quantiles(values, n=100, method="inclusive")[p - 1]


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


@dataclass(frozen=True)
class TurnLatency:
    """The latency of one completed turn, in seconds."""

    speech_id: str
    stages: dict[str, float]

    @property
    def e2e(self) -> float:
        return self.stages["e2e"]


@dataclass(frozen=True)
class Percentiles:
    """p50 and p90 in seconds; both `None` when there is nothing to measure."""

    p50: float | None = None
    p90: float | None = None


@dataclass(frozen=True)
class LatencySummary:
    turns: int = 0
    stages: dict[str, Percentiles] = field(default_factory=lambda: {s: Percentiles() for s in STAGES})

    @property
    def e2e(self) -> Percentiles:
        return self.stages["e2e"]

    @property
    def p50_ms(self) -> int | None:
        """End of speech → first agent audio, median, in whole milliseconds."""
        return None if self.e2e.p50 is None else _ms(self.e2e.p50)

    @property
    def p90_ms(self) -> int | None:
        return None if self.e2e.p90 is None else _ms(self.e2e.p90)

    def stage_latencies(self) -> dict[str, StageLatency]:
        """Per-stage p50/p90 in milliseconds as the outcome contract type (stages without data left out)."""
        return {
            name: StageLatency(p50_ms=_ms(p.p50), p90_ms=_ms(p.p90))
            for name, p in self.stages.items()
            if p.p50 is not None and p.p90 is not None
        }

    def to_outcome(self) -> dict[str, object]:
        """The `CallOutcome` fields: `latency_p50`/`latency_p90` (ms) and `latency_stages` (for traces)."""
        return {
            "latency_p50": self.p50_ms,
            "latency_p90": self.p90_ms,
            "latency_stages": {
                name: {"p50_ms": s.p50_ms, "p90_ms": s.p90_ms} for name, s in self.stage_latencies().items()
            },
        }


@dataclass
class _Partial:
    eou: float | None = None
    transcription: float = 0.0
    llm_ttfts: list[float] = field(default_factory=list)
    tts_ttfb: float | None = None


class LatencyTracker:
    """Collects LiveKit metrics for one call and computes per-turn and summary latency."""

    def __init__(self) -> None:
        self._pending: dict[str, _Partial] = {}
        self._done: dict[str, TurnLatency] = {}

    def add(self, metric: object) -> None:
        """Feed one metrics object (the `metrics` of a `metrics_collected` event). Other types are ignored."""
        if isinstance(metric, metrics.EOUMetrics):
            speech_id = metric.speech_id
            if speech_id is None or speech_id in self._done:
                return
            part = self._partial(speech_id)
            part.eou = metric.end_of_utterance_delay
            part.transcription = metric.transcription_delay
        elif isinstance(metric, metrics.LLMMetrics):
            speech_id = metric.speech_id
            if speech_id is None or speech_id in self._done or metric.cancelled:
                return
            self._partial(speech_id).llm_ttfts.append(metric.ttft)
        elif isinstance(metric, metrics.TTSMetrics):
            speech_id = metric.speech_id
            if speech_id is None or speech_id in self._done or metric.cancelled:
                return
            part = self._partial(speech_id)
            if part.tts_ttfb is None:  # the first segment sets the time to first audio
                part.tts_ttfb = metric.ttfb
        else:
            return
        self._complete_if_ready(speech_id)

    def turns(self) -> list[TurnLatency]:
        """Completed turns, in the order they completed."""
        return list(self._done.values())

    def summary(self) -> LatencySummary:
        turns = self.turns()
        stages: dict[str, Percentiles] = {}
        for name in STAGES:
            values = [t.stages[name] for t in turns]
            stages[name] = (
                Percentiles(percentile(values, 50), percentile(values, 90)) if values else Percentiles()
            )
        return LatencySummary(turns=len(turns), stages=stages)

    # ------------------------------------------------------------------ internals

    def _partial(self, speech_id: str) -> _Partial:
        part = self._pending.get(speech_id)
        if part is None:
            if len(self._pending) >= MAX_PENDING_TURNS:
                self._pending.pop(next(iter(self._pending)))  # forget the oldest turn that never completed
            part = self._pending[speech_id] = _Partial()
        return part

    def _complete_if_ready(self, speech_id: str) -> None:
        part = self._pending.get(speech_id)
        if part is None or part.eou is None or not part.llm_ttfts or part.tts_ttfb is None:
            return
        del self._pending[speech_id]
        llm_ttft = sum(part.llm_ttfts)
        stages = {
            "eou": part.eou,
            "stt": part.transcription,
            "llm_ttft": llm_ttft,
            "tts_ttfb": part.tts_ttfb,
            "e2e": part.eou + llm_ttft + part.tts_ttfb,
        }
        self._done[speech_id] = TurnLatency(speech_id=speech_id, stages=stages)
        log.info(
            "turn latency",
            extra={
                "speech_id": speech_id,
                "eou_ms": _ms(stages["eou"]),
                "stt_ms": _ms(stages["stt"]),
                "llm_ttft_ms": _ms(stages["llm_ttft"]),
                "tts_ttfb_ms": _ms(stages["tts_ttfb"]),
                "e2e_ms": _ms(stages["e2e"]),
            },
        )


def attach_latency_tracker(session: AgentSession, tracker: LatencyTracker) -> None:
    """Feed the tracker from the session's `metrics_collected` events."""

    def on_metrics(event: MetricsCollectedEvent) -> None:
        tracker.add(event.metrics)

    session.on("metrics_collected", on_metrics)
