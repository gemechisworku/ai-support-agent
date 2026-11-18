"""T-VOICE-02 · VOICE-R2/R3: the provider factory builds everything from Settings alone.

No network: the tests only construct plugin objects (no request is made until a stream starts).
"""

import pytest
from livekit.agents import stt as lk_stt
from livekit.plugins import anthropic, openai
from pydantic import ValidationError

from supportagent.common.settings import ModelsSettings, Settings
from supportagent.observability.versions import model_version as obs_model_version
from supportagent.voice import providers
from supportagent.voice.stt_filter import NonSpeechFilterSTT


def _with_models(base: Settings, **models: object) -> Settings:
    """`base` (environment-only settings, from the `settings` fixture) with some model settings replaced."""
    merged = ModelsSettings(_env_file=None, **{**base.models.model_dump(), **models})  # type: ignore[arg-type]
    return base.model_copy(update={"models": merged})


def _base_url(client_owner: object) -> str:
    client = client_owner._client  # type: ignore[attr-defined]
    return str(client.base_url).rstrip("/")


def test_local_llm_is_the_openai_plugin_pointed_at_llm_url(settings: Settings) -> None:
    s = _with_models(settings, llm_url="http://127.0.0.1:9999/v1", llm_model="my-llama")
    llm = providers.build_llm(s)
    assert isinstance(llm, openai.LLM)
    assert llm.model == "my-llama"
    assert _base_url(llm) == "http://127.0.0.1:9999/v1"


def test_default_llm_uses_settings_defaults(settings: Settings) -> None:
    s = settings
    llm = providers.build_llm(s)
    assert isinstance(llm, openai.LLM)
    assert llm.model == s.models.llm_model
    assert _base_url(llm) == s.models.llm_url.rstrip("/")


def test_stt_is_whisper_behind_openai_plugin(settings: Settings) -> None:
    s = _with_models(settings, stt_url="http://127.0.0.1:8178")
    stt = providers.build_stt(s)
    assert isinstance(stt, NonSpeechFilterSTT)  # whisper's [BLANK_AUDIO] tags are removed (stt_filter)
    assert isinstance(stt.inner, openai.STT)
    assert _base_url(stt.inner) == "http://127.0.0.1:8178/v1"


def test_stt_is_wrapped_in_a_stream_adapter_when_given_a_vad(settings: Settings) -> None:
    s = settings

    class _FakeVad:  # StreamAdapter only stores the VAD until a stream starts
        capabilities = None

    stt = providers.build_stt(s, vad=_FakeVad())  # type: ignore[arg-type]
    assert isinstance(stt, lk_stt.StreamAdapter)
    assert isinstance(stt.wrapped_stt, NonSpeechFilterSTT)
    assert isinstance(stt.wrapped_stt.inner, openai.STT)


def test_tts_is_kokoro_behind_openai_plugin(settings: Settings) -> None:
    s = _with_models(settings, tts_url="http://127.0.0.1:8880", tts_voice="am_adam")
    tts = providers.build_tts(s)
    assert isinstance(tts, openai.TTS)
    assert _base_url(tts) == "http://127.0.0.1:8880/v1"
    assert tts._opts.voice == "am_adam"
    assert tts._opts.model == s.models.tts_model


def test_anthropic_switch_returns_the_anthropic_llm(settings: Settings) -> None:
    s = _with_models(settings, llm_provider="anthropic", anthropic_api_key="sk-test-synthetic")
    llm = providers.build_llm(s)
    assert isinstance(llm, anthropic.LLM)
    assert llm.model == "claude-haiku-5-5"


def test_anthropic_without_key_fails_at_settings_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY is required"):
        ModelsSettings(llm_provider="anthropic", anthropic_api_key=None)


@pytest.mark.parametrize(
    ("models", "expected"),
    [
        ({}, "llamacpp/llama-3.1-8b-instruct"),
        ({"llm_model": "llama-3.2-3b-instruct"}, "llamacpp/llama-3.2-3b-instruct"),
        (
            {"llm_provider": "anthropic", "anthropic_api_key": "sk-test-synthetic"},
            "anthropic/claude-haiku-5-5",
        ),
    ],
)
def test_model_version_covers_both_providers(
    settings: Settings, models: dict[str, object], expected: str
) -> None:
    s = _with_models(settings, **models)
    assert providers.model_version(s) == expected
    # OBS version stamping embeds the same provider/model id.
    assert obs_model_version(s.models).startswith(f"llm:{expected};")


def test_vad_and_turn_detector_builders_are_lazy() -> None:
    # Both load model weights, so the factory only returns callables bound to settings here;
    # loading happens in the worker's prewarm (T-AGENT-01 / T-VOICE-03).
    assert callable(providers.build_vad)
    assert callable(providers.build_turn_detector)
