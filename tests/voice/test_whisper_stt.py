"""T-VOICE-02: whisper's hallucinated "Thank you." for noise is dropped like silence.

Measured against the local whisper-server on 2026-11-18 (generated noise and Kokoro speech, never user audio):
3 s of low-level noise came back as " Thank you." with no_speech_prob 0.927 and avg_logprob -0.55; synthetic
speech scored no_speech_prob 0.001 ("What is the late fee on a personal loan?") and 0.009 ("Thank you.").
The payloads below have that shape.
"""

import json
import logging

import httpx
import pytest
from livekit import rtc
from livekit.agents import APIConnectionError, APIStatusError
from livekit.agents import stt as lk_stt

from supportagent.voice.whisper_stt import Verdict, WhisperCppSTT, judge


def _payload(*segments: dict[str, object]) -> dict[str, object]:
    return {"text": " ".join(str(s["text"]) for s in segments), "segments": list(segments)}


NOISE = _payload({"text": " Thank you.", "no_speech_prob": 0.927, "avg_logprob": -0.55})
SPEECH = _payload({"text": " Thank you.", "no_speech_prob": 0.009, "avg_logprob": -0.06})
QUESTION = _payload(
    {"text": " What is the late fee on a personal loan?", "no_speech_prob": 0.001, "avg_logprob": -0.01}
)


def test_noise_that_whisper_wrote_as_thank_you_is_dropped() -> None:
    verdict = judge(NOISE)
    assert verdict == Verdict("", "no_speech", 1, 0.927, -0.55)
    assert verdict.kept is False


def test_a_genuine_thank_you_is_kept() -> None:
    verdict = judge(SPEECH)
    assert verdict.kept and verdict.text == "Thank you."


def test_a_question_is_kept_and_stripped() -> None:
    assert judge(QUESTION).text == "What is the late fee on a personal loan?"


def test_a_very_uncertain_segment_is_dropped() -> None:
    verdict = judge(_payload({"text": " mumble", "no_speech_prob": 0.1, "avg_logprob": -2.4}))
    assert verdict.reason == "low_confidence" and verdict.text == ""


def test_only_the_segments_that_pass_are_used() -> None:
    mixed = _payload(
        {"text": " What is my balance?", "no_speech_prob": 0.02, "avg_logprob": -0.2},
        {"text": " Thank you.", "no_speech_prob": 0.95, "avg_logprob": -0.6},
    )
    verdict = judge(mixed)
    assert verdict.text == "What is my balance?" and verdict.no_speech_prob == 0.95


def test_the_thresholds_are_settings_not_constants() -> None:
    assert judge(SPEECH, no_speech_max=0.001).reason == "no_speech"
    assert judge(NOISE, no_speech_max=0.99).kept is True


def test_a_server_that_sends_no_segments_is_trusted() -> None:
    assert judge({"text": " Hello there.\n"}).text == "Hello there."
    assert judge({"text": "  "}).reason == "empty"
    assert judge({}).reason == "empty"


def _frame(seconds: float = 1.0) -> rtc.AudioFrame:
    samples = int(16000 * seconds)
    return rtc.AudioFrame(
        data=bytes(samples * 2), sample_rate=16000, num_channels=1, samples_per_channel=samples
    )


def _stt(
    payload: dict[str, object] | None = None, status: int = 200
) -> tuple[WhisperCppSTT, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload if payload is not None else {})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WhisperCppSTT(base_url="http://127.0.0.1:8178/v1", client=client), seen


async def test_noise_returns_no_alternatives_so_silence_stays_silence() -> None:
    stt, seen = _stt(NOISE)
    event = await stt.recognize([_frame(3.0)])
    assert event.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT
    assert event.alternatives == []
    assert seen[0].url == "http://127.0.0.1:8178/v1/audio/transcriptions"
    assert b"verbose_json" in seen[0].content  # the scores are what the verdict is made from


async def test_speech_returns_the_text() -> None:
    stt, _ = _stt(QUESTION)
    event = await stt.recognize([_frame()])
    assert [a.text for a in event.alternatives] == ["What is the late fee on a personal loan?"]


async def test_each_result_is_logged_with_numbers_and_never_the_words(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="supportagent.voice.whisper_stt")
    stt, _ = _stt(QUESTION)
    await stt.recognize([_frame(2.0)])
    stt_noise, _ = _stt(NOISE)
    await stt_noise.recognize([_frame(3.0)])
    records = [r for r in caplog.records if r.getMessage() == "stt result"]
    assert len(records) == 2
    kept, dropped = records
    assert (kept.kept, kept.words, kept.audio_s) == (True, 9, 2.0)  # type: ignore[attr-defined]
    assert (dropped.kept, dropped.dropped_because, dropped.no_speech_prob) == (False, "no_speech", 0.927)  # type: ignore[attr-defined]
    assert "late fee" not in caplog.text and "Thank" not in caplog.text


async def test_a_server_error_is_an_api_error_without_the_body() -> None:
    stt, _ = _stt({"error": "secret detail"}, status=500)
    with pytest.raises((APIStatusError, APIConnectionError)) as caught:
        await stt.recognize([_frame()])
    assert "secret detail" not in str(caught.value)


def test_the_response_shape_matches_what_whisper_server_returns() -> None:
    """The real response (trimmed): extra keys such as `words` and `language_probabilities` are ignored."""
    real = json.loads(
        '{"task":"transcribe","language":"english","duration":3.0,"text":" Thank you.\\n",'
        '"segments":[{"id":0,"text":" Thank you.","start":0.0,"end":3.0,"tokens":[6952,345,13],'
        '"words":[{"word":" Thank","probability":0.09}],"temperature":0.0,"avg_logprob":-0.5499,'
        '"no_speech_prob":0.9268}],"detected_language":"lithuanian"}'
    )
    assert judge(real).reason == "no_speech"
