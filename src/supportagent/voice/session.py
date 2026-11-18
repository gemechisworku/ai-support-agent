"""`AgentSession` options for the cascaded pipeline (VOICE-R4), built from `Settings` alone.

    opts = build_session_options(get_settings(), vad=prewarmed_vad)
    session = AgentSession(**opts)
    attach_silence_policy(session, SilencePolicy(...), on_reprompt, on_end)   # see silence.py

- STT, LLM and TTS come from `providers`. STT is VAD-segmented (whisper is not streaming).
- Turn taking: Silero VAD plus LiveKit's model-based end-of-turn detector; the wait after speech is
  `ENDPOINTING_MIN_DELAY_S` .. `ENDPOINTING_MAX_DELAY_S`.
- Barge-in: interruptions are on, and `INTERRUPTION_MIN_S` of caller speech stops the agent mid-sentence.
- TTS is fed sentence by sentence: Kokoro does not stream input, so a sentence tokenizer sits in front of it
  and each sentence is synthesised and spoken as soon as the LLM has completed it.
- The session's own "user away" timer is off: caller silence is handled by `silence.SilencePolicy`, whose
  wording the agent owns.

LiveKit imports live here and in `providers.py` only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from livekit.agents import tokenize
from livekit.agents import tts as lk_tts
from livekit.agents import vad as lk_vad

from supportagent.common.settings import Settings
from supportagent.voice import providers

# Sentences shorter than this are merged with the next one ("Okay." + "Let me check."), which keeps Kokoro
# from speaking one-word fragments with a stutter between them.
MIN_SENTENCE_LEN = 20


def sentence_tokenizer() -> tokenize.SentenceTokenizer:
    return tokenize.basic.SentenceTokenizer(min_sentence_len=MIN_SENTENCE_LEN)


def build_session_options(
    settings: Settings,
    *,
    vad: lk_vad.VAD | None = None,
    turn_detector: Any | None = None,
) -> dict[str, Any]:
    """The keyword arguments for `AgentSession(...)`.

    `vad` and `turn_detector` default to the provider loaders (which load model weights): the worker passes
    in the ones it prewarmed, and tests pass stand-ins.
    """
    m = settings.models
    vad = vad if vad is not None else providers.build_vad()
    turn_detector = turn_detector if turn_detector is not None else providers.build_turn_detector()
    tts = lk_tts.StreamAdapter(tts=providers.build_tts(settings), sentence_tokenizer=sentence_tokenizer())
    return {
        "stt": providers.build_stt(settings, vad=vad),
        "llm": providers.build_llm(settings),
        "tts": tts,
        "vad": vad,
        "turn_handling": {
            "turn_detection": turn_detector,
            "endpointing": {
                "mode": "fixed",
                "min_delay": m.endpointing_min_delay_s,
                "max_delay": m.endpointing_max_delay_s,
            },
            "interruption": {"enabled": True, "min_duration": m.interruption_min_s},
        },
        "user_away_timeout": None,
    }


def barge_in_latency_s(events: Sequence[Mapping[str, Any]]) -> float | None:
    """Seconds from the caller starting to speak over the agent until the agent stops speaking.

    `events` are session `user_state_changed` / `agent_state_changed` events as dicts with `type`,
    `new_state` and `created_at` (the form `scripts/` loggers write). Only a user turn that begins while the
    agent is `speaking` counts. Returns the first such latency, or None when the caller never interrupted.
    Used by the live barge-in check (VOICE-R4: < 0.3 s).
    """
    agent_speaking = False
    interrupted_at: float | None = None
    for ev in sorted(events, key=lambda e: e["created_at"]):
        if ev["type"] == "agent_state_changed":
            was_speaking, agent_speaking = agent_speaking, ev["new_state"] == "speaking"
            if was_speaking and not agent_speaking and interrupted_at is not None:
                return float(ev["created_at"]) - interrupted_at
        elif ev["type"] == "user_state_changed" and ev["new_state"] == "speaking" and agent_speaking:
            interrupted_at = float(ev["created_at"])
    return None
