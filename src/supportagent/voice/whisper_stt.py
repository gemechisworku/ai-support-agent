"""whisper.cpp as an STT that asks whether anyone was speaking (T-VOICE-02).

On the fourth real call the caller's turns came back as "Thank you." and "Thanks.": whisper hallucinates a
polite phrase for noise. A 3 s clip of low-level noise returns exactly `" Thank you."` with `no_speech_prob`
0.93, while synthetic speech, even a real "Thank you.", scores 0.001 to 0.009 (measured against the local
`whisper-server`). The server reports this in its `verbose_json` response, but LiveKit's OpenAI plugin only
asks for that format for the model named "whisper-1" and then throws the segments away, so this module calls
the server itself and judges every result:

- a segment whose `no_speech_prob` is above `no_speech_max` is not speech (default 0.6, whisper's cut-off);
- a segment whose `avg_logprob` is below `min_avg_logprob` is too uncertain to act on (default -1.5);
- text from the segments that pass is the transcript; when none pass, the result has no alternatives, which
  `StreamAdapter` (and so the session) ignores: silence stays silence and the silence timer is not reset.

One INFO line per result carries sizes and scores, never the words, so a bad call can be diagnosed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from livekit import rtc
from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError, LanguageCode
from livekit.agents import stt as lk_stt
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions, NotGivenOr
from livekit.agents.utils import AudioBuffer

from supportagent.common.logging import get_logger

log = get_logger(__name__)

DEFAULT_NO_SPEECH_MAX = 0.6
DEFAULT_MIN_AVG_LOGPROB = -1.5
REQUEST_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class Verdict:
    """What to do with one whisper response, and the numbers behind it (never the words)."""

    text: str  # the transcript to use ("" when dropped)
    reason: str | None  # why it was dropped: "no_speech", "low_confidence" or "empty"; None when kept
    segments: int
    no_speech_prob: float | None  # the highest over the segments, None when the server sent none
    avg_logprob: float | None  # the lowest over the segments

    @property
    def kept(self) -> bool:
        return self.reason is None


def judge(
    payload: Mapping[str, Any],
    *,
    no_speech_max: float = DEFAULT_NO_SPEECH_MAX,
    min_avg_logprob: float = DEFAULT_MIN_AVG_LOGPROB,
) -> Verdict:
    """Decide from a `verbose_json` response whether this was speech. Pure: no I/O, no logging."""
    segments = [s for s in payload.get("segments") or [] if isinstance(s, Mapping)]
    full_text = str(payload.get("text") or "").strip()
    if not segments:  # a server that sends no scores: nothing to judge by, so trust the text
        return Verdict(full_text, None if full_text else "empty", 0, None, None)

    no_speech = [float(s["no_speech_prob"]) for s in segments if s.get("no_speech_prob") is not None]
    logprobs = [float(s["avg_logprob"]) for s in segments if s.get("avg_logprob") is not None]
    worst_no_speech = max(no_speech) if no_speech else None
    worst_logprob = min(logprobs) if logprobs else None

    kept: list[str] = []
    saw_no_speech = saw_unsure = False
    for s in segments:
        text = str(s.get("text") or "").strip()
        if not text:
            continue
        if s.get("no_speech_prob") is not None and float(s["no_speech_prob"]) > no_speech_max:
            saw_no_speech = True
        elif s.get("avg_logprob") is not None and float(s["avg_logprob"]) < min_avg_logprob:
            saw_unsure = True
        else:
            kept.append(text)
    if kept:
        return Verdict(" ".join(kept), None, len(segments), worst_no_speech, worst_logprob)
    reason = "no_speech" if saw_no_speech else "low_confidence" if saw_unsure else "empty"
    return Verdict("", reason, len(segments), worst_no_speech, worst_logprob)


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


class WhisperCppSTT(lk_stt.STT):
    """`whisper-server`'s OpenAI-compatible endpoint, with a speech / no-speech verdict on every result."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str = "whisper-1",
        language: str = "en",
        no_speech_max: float = DEFAULT_NO_SPEECH_MAX,
        min_avg_logprob: float = DEFAULT_MIN_AVG_LOGPROB,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(capabilities=lk_stt.STTCapabilities(streaming=False, interim_results=False))
        self._url = f"{base_url.rstrip('/')}/audio/transcriptions"
        self._model = model
        self._language = language
        self._no_speech_max = no_speech_max
        self._min_avg_logprob = min_avg_logprob
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=5.0))

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "whisper.cpp"

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> lk_stt.SpeechEvent:
        frame = rtc.combine_audio_frames(buffer)
        seconds = frame.samples_per_channel / frame.sample_rate if frame.sample_rate else 0.0
        lang = language if isinstance(language, str) else self._language
        try:
            response = await self._client.post(
                self._url,
                files={"file": ("file.wav", frame.to_wav_bytes(), "audio/wav")},
                data={"model": self._model, "language": lang, "response_format": "verbose_json"},
                timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=conn_options.timeout),
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            raise APITimeoutError() from None
        except httpx.HTTPStatusError as exc:
            raise APIStatusError(
                "whisper-server returned an error", status_code=exc.response.status_code
            ) from None
        except Exception as exc:
            raise APIConnectionError() from exc

        verdict = judge(
            payload if isinstance(payload, Mapping) else {},
            no_speech_max=self._no_speech_max,
            min_avg_logprob=self._min_avg_logprob,
        )
        log.info(
            "stt result",
            extra={
                "kept": verdict.kept,
                "dropped_because": verdict.reason,
                "audio_s": round(seconds, 2),
                "chars": len(verdict.text),
                "words": len(verdict.text.split()),
                "segments": verdict.segments,
                "no_speech_prob": _rounded(verdict.no_speech_prob, 3),
                "avg_logprob": _rounded(verdict.avg_logprob, 3),
            },
        )
        if not verdict.kept:
            return lk_stt.SpeechEvent(type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[])
        return lk_stt.SpeechEvent(
            type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[lk_stt.SpeechData(text=verdict.text, language=LanguageCode(lang))],
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        await super().aclose()
