"""T-VOICE-03 · VOICE-R4: `build_session_options` builds the AgentSession kwargs from Settings alone.

No network, no model weights: the VAD and the turn detector are passed in as stand-ins, and building
STT/LLM/TTS objects opens no connection (see test_providers.py).
"""

import asyncio
import inspect

import pytest
from livekit.agents import AgentSession
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from livekit.plugins import openai
from pydantic import ValidationError

from supportagent.common.settings import ModelsSettings, Settings
from supportagent.voice import session as session_module
from supportagent.voice.session import barge_in_latency_s, build_session_options
from supportagent.voice.stt_filter import NonSpeechFilterSTT


class _FakeVad:
    capabilities = None


class _FakeTurnDetector:
    pass


VAD = _FakeVad()
TURN = _FakeTurnDetector()


def _with_models(base: Settings, **models: object) -> Settings:
    merged = ModelsSettings(_env_file=None, **{**base.models.model_dump(), **models})  # type: ignore[arg-type]
    return base.model_copy(update={"models": merged})


def options(settings: Settings) -> dict[str, object]:
    return build_session_options(settings, vad=VAD, turn_detector=TURN)  # type: ignore[arg-type]


def test_options_include_vad_model_turn_detection_and_interruptions(settings: Settings) -> None:
    o = options(settings)
    assert o["vad"] is VAD
    handling = o["turn_handling"]
    assert handling["turn_detection"] is TURN  # type: ignore[index]
    assert handling["interruption"]["enabled"] is True  # type: ignore[index]


def test_configured_delays_are_used(settings: Settings) -> None:
    s = _with_models(
        settings, endpointing_min_delay_s=0.7, endpointing_max_delay_s=2.5, interruption_min_s=0.6
    )
    handling = options(s)["turn_handling"]
    assert handling["endpointing"] == {"mode": "fixed", "min_delay": 0.7, "max_delay": 2.5}  # type: ignore[index]
    assert handling["interruption"]["min_duration"] == 0.6  # type: ignore[index]


def test_default_delays_match_the_plan(settings: Settings) -> None:
    handling = options(settings)["turn_handling"]
    assert handling["endpointing"]["min_delay"] == 0.4  # type: ignore[index]
    assert handling["interruption"]["min_duration"] == 0.4  # type: ignore[index]


def test_stt_llm_tts_come_from_the_provider_factory(settings: Settings) -> None:
    o = options(settings)
    assert isinstance(o["stt"], lk_stt.StreamAdapter)  # VAD-segmented whisper
    assert isinstance(o["stt"].wrapped_stt, NonSpeechFilterSTT)  # type: ignore[attr-defined]
    assert isinstance(o["stt"].wrapped_stt.inner, openai.STT)  # type: ignore[attr-defined]
    assert isinstance(o["llm"], openai.LLM)
    assert o["llm"].model == settings.models.llm_model  # type: ignore[attr-defined]


def test_tts_is_wrapped_in_a_sentence_stream_adapter(settings: Settings) -> None:
    tts = options(settings)["tts"]
    assert isinstance(tts, lk_tts.StreamAdapter)
    assert isinstance(tts._wrapped_tts, openai.TTS)  # type: ignore[attr-defined]  # no public accessor


async def test_sentences_are_released_as_soon_as_they_complete() -> None:
    """The first sentence reaches TTS before the LLM has finished the rest of the reply."""
    stream = session_module.sentence_tokenizer().stream()
    stream.push_text("Your payment of $200.72 was due on 4 November. ")
    stream.push_text("It is now five days")
    first = await asyncio.wait_for(stream.__anext__(), timeout=2)  # the LLM is still mid-sentence
    assert first.token.startswith("Your payment of $200.72 was due on 4 November.")
    stream.push_text(" late. Would you like to pay today?")
    stream.end_input()
    rest = [ev.token async for ev in stream]
    assert "".join(rest).strip().endswith("Would you like to pay today?")


def test_the_sessions_own_away_timeout_is_off_because_silence_is_ours(settings: Settings) -> None:
    assert options(settings)["user_away_timeout"] is None


def test_every_option_is_a_real_agent_session_parameter(settings: Settings) -> None:
    """Guards against LiveKit renaming a keyword: the kwargs must bind to AgentSession.__init__."""
    inspect.signature(AgentSession.__init__).bind(object(), **options(settings))


def test_vad_and_turn_detector_default_to_the_provider_loaders(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module.providers, "build_vad", lambda: VAD)
    monkeypatch.setattr(session_module.providers, "build_turn_detector", lambda: TURN)
    o = build_session_options(settings)
    assert o["vad"] is VAD
    assert o["turn_handling"]["turn_detection"] is TURN  # type: ignore[index]


def test_settings_reject_inverted_timings() -> None:
    with pytest.raises(ValidationError):
        ModelsSettings(_env_file=None, endpointing_min_delay_s=2, endpointing_max_delay_s=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ModelsSettings(_env_file=None, silence_reprompt_s=10, silence_end_s=10)  # type: ignore[call-arg]


# ------------------------------------------------------------------ barge-in timing helper


def test_barge_in_latency_is_user_speech_to_agent_leaving_speaking() -> None:
    events = [
        {
            "type": "agent_state_changed",
            "old_state": "listening",
            "new_state": "speaking",
            "created_at": 10.0,
        },
        {
            "type": "user_state_changed",
            "old_state": "listening",
            "new_state": "speaking",
            "created_at": 11.00,
        },
        {
            "type": "agent_state_changed",
            "old_state": "speaking",
            "new_state": "listening",
            "created_at": 11.21,
        },
    ]
    assert barge_in_latency_s(events) == pytest.approx(0.21)


def test_barge_in_latency_none_when_the_caller_never_interrupted() -> None:
    events = [
        {
            "type": "agent_state_changed",
            "old_state": "listening",
            "new_state": "speaking",
            "created_at": 10.0,
        },
        {
            "type": "agent_state_changed",
            "old_state": "speaking",
            "new_state": "listening",
            "created_at": 14.0,
        },
        {"type": "user_state_changed", "old_state": "listening", "new_state": "speaking", "created_at": 15.0},
    ]
    assert barge_in_latency_s(events) is None
