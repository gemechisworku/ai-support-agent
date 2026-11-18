"""Drop whisper.cpp's non-speech annotations before they become a caller turn (T-VOICE-02).

For silence and noise whisper.cpp writes tags such as `[BLANK_AUDIO]`, `[LAUGHTER]`, `(audience chattering)`
or `*music*` as if they were a transcript. Left alone, each becomes a "caller turn": the agent answers it, and
the silence policy sees speech. `NonSpeechFilterSTT` wraps the recogniser and strips the tags. A result that
is nothing but tags comes back with no alternatives, which `StreamAdapter` (and so the session) ignores:
silence stays silence, so the re-prompt timer is not reset.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from livekit.agents import stt as lk_stt
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions, NotGivenOr
from livekit.agents.utils import AudioBuffer

# `[BLANK_AUDIO]`, `[ laughter ]`, `(audience chattering)`, `*music*`: a bracketed, parenthesised or starred
# tag of at most 60 characters with no nested brackets. Real speech with brackets is rare in a phone call.
_TAG = re.compile(r"\[[^\[\]]{1,60}\]|\([^()]{1,60}\)|\*[^*\n]{1,60}\*")
_SPACES = re.compile(r"[ \t]{2,}")
_ONLY_PUNCTUATION = re.compile(r"^[\W_]*$")


def strip_non_speech(text: str) -> str:
    """The text without annotation tags; empty when nothing a person said is left."""
    cleaned = _SPACES.sub(" ", _TAG.sub(" ", text)).strip()
    return "" if _ONLY_PUNCTUATION.match(cleaned) else cleaned


class NonSpeechFilterSTT(lk_stt.STT):
    """Wraps an STT and removes annotation tags from every final result."""

    def __init__(self, inner: lk_stt.STT) -> None:
        super().__init__(capabilities=inner.capabilities)
        self._inner = inner

    @property
    def inner(self) -> lk_stt.STT:
        return self._inner

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def provider(self) -> str:
        return self._inner.provider

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> lk_stt.SpeechEvent:
        event = await self._inner.recognize(buffer, language=language, conn_options=conn_options)
        kept: list[Any] = []
        for alternative in event.alternatives:
            text = strip_non_speech(alternative.text)
            if text:
                kept.append(replace(alternative, text=text))
        return replace(event, alternatives=kept)

    async def aclose(self) -> None:
        await self._inner.aclose()
        await super().aclose()
