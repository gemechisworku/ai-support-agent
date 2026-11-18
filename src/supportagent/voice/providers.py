"""Streaming STT, LLM and TTS components built from `Settings` alone (VOICE-R2, VOICE-R3).

The local servers are OpenAI-compatible, so one plugin (`livekit-plugins-openai`) covers all three:

- STT: whisper.cpp `whisper-server` (`--inference-path /v1/audio/transcriptions`) via `openai.STT`.
  Whisper is not streaming, so with a VAD the STT is wrapped in `stt.StreamAdapter` (VAD-segmented).
- LLM: llama.cpp `llama-server --jinja` via `openai.LLM(base_url=LLM_URL, model=LLM_MODEL)`;
  `LLM_PROVIDER=anthropic` switches to `anthropic.LLM(model=ANTHROPIC_MODEL)` (F14).
- TTS: Kokoro-FastAPI via `openai.TTS(model=TTS_MODEL, voice=TTS_VOICE)`.

Usage::

    s = get_settings()
    vad = build_vad()                       # loads Silero weights: call it in the worker's prewarm
    session = AgentSession(stt=build_stt(s, vad=vad), llm=build_llm(s), tts=build_tts(s), vad=vad,
                           turn_detection=build_turn_detector())
    versions.model == f"llm:{model_version(s)};…"

Every URL, model name and voice comes from `Settings`; building an object opens no connection.
LiveKit plugin imports live here (and in `session.py`) only, because their APIs move quickly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from livekit.agents import vad as lk_vad
from livekit.plugins import anthropic, openai

from supportagent.common.settings import Settings
from supportagent.voice.stt_filter import NonSpeechFilterSTT

if TYPE_CHECKING:
    from livekit.plugins.turn_detector.english import EnglishModel

# llama-server and whisper/kokoro ignore the key, but the OpenAI client requires a non-empty one.
LOCAL_API_KEY = "local"


def _v1(url: str) -> str:
    """`http://host:port` or `…/v1` → `…/v1` (STT_URL / TTS_URL are server roots)."""
    url = url.rstrip("/")
    return url if url.endswith("/v1") else f"{url}/v1"


def build_stt(settings: Settings, *, vad: lk_vad.VAD | None = None) -> lk_stt.STT:
    """whisper.cpp through the OpenAI STT plugin, with its non-speech tags (`[BLANK_AUDIO]`...) removed
    (`stt_filter`); wrapped in a `StreamAdapter` when a VAD is given."""
    m = settings.models
    stt = openai.STT(
        model=m.stt_model,
        language=m.stt_language,
        base_url=_v1(m.stt_url),
        api_key=LOCAL_API_KEY,
        use_realtime=False,
    )
    filtered = NonSpeechFilterSTT(stt)
    return lk_stt.StreamAdapter(stt=filtered, vad=vad) if vad is not None else filtered


def build_llm(settings: Settings) -> lk_llm.LLM:
    """Llama 3.1 8B on llama.cpp by default; the hosted Anthropic model when `LLM_PROVIDER=anthropic`."""
    m = settings.models
    if m.llm_provider == "anthropic":
        # Settings validation guarantees the key is present (ModelsSettings._hosted_llm_needs_key).
        assert m.anthropic_api_key is not None
        return anthropic.LLM(model=m.anthropic_model, api_key=m.anthropic_api_key.get_secret_value())
    return openai.LLM(model=m.llm_model, base_url=m.llm_url, api_key=LOCAL_API_KEY)


def build_tts(settings: Settings) -> lk_tts.TTS:
    """Kokoro-FastAPI through the OpenAI TTS plugin."""
    m = settings.models
    return openai.TTS(model=m.tts_model, voice=m.tts_voice, base_url=_v1(m.tts_url), api_key=LOCAL_API_KEY)


def build_vad() -> lk_vad.VAD:
    """Silero VAD. Loads model weights, so call it once per process (the worker's `prewarm`)."""
    from livekit.plugins import silero

    return silero.VAD.load()


def build_turn_detector() -> EnglishModel:
    """LiveKit's English end-of-turn model. Its files come from `python -m supportagent.voice.turn_files`."""
    from supportagent.voice import turn_files

    missing = turn_files.missing_files()
    if missing:  # the plugin would raise a cryptic LocalEntryNotFoundError and the call would die silently
        raise RuntimeError(
            f"the end-of-turn model files are not downloaded ({', '.join(missing)}): "
            "run `uv run python -m supportagent.voice.turn_files` (or `make agent-dev`, which does it)"
        )
    from livekit.plugins.turn_detector.english import EnglishModel

    return EnglishModel()


def model_version(settings: Settings) -> str:
    """The active LLM as `provider/model`, e.g. `llamacpp/llama-3.1-8b-instruct` (VOICE-R3).

    OBS version stamping (`observability.versions.model_version`) embeds the same id as its `llm:` part.
    """
    return settings.models.llm_id
