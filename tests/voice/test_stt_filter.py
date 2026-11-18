"""T-VOICE-02: whisper.cpp's non-speech annotations never become a caller turn.

Found on a real call: the agent treated "[BLANK_AUDIO]", "(audience chattering)" and "[LAUGHTER]" as
things the caller said."""

import pytest
from livekit.agents import stt as lk_stt
from livekit.agents.types import APIConnectOptions

from supportagent.voice.stt_filter import NonSpeechFilterSTT, strip_non_speech


@pytest.mark.parametrize(
    "text",
    [
        "[BLANK_AUDIO]",
        " [blank_audio] ",
        "[LAUGHTER]",
        "[SOUND]",
        "[MUSIC]",
        "(audience chattering)",
        "(muffled speaking)",
        "*music*",
        "[BLANK_AUDIO] [BLANK_AUDIO]",
        "(audience chattering) [LAUGHTER]",
        "",
        "   ",
        "...",
        " - ",
    ],
)
def test_annotation_only_text_becomes_empty(text: str) -> None:
    assert strip_non_speech(text) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What is the late fee? [BLANK_AUDIO]", "What is the late fee?"),
        ("(muffled speaking) I want my balance", "I want my balance"),
        ("Hello [SOUND] there", "Hello there"),
        ("I owe about $200.72", "I owe about $200.72"),
        ("Yes, yes, I am still there.", "Yes, yes, I am still there."),
        ("so what does it say\n for me", "so what does it say\n for me"),
    ],
)
def test_real_words_are_kept_and_tags_removed(text: str, expected: str) -> None:
    assert strip_non_speech(text) == expected


class _FakeSTT(lk_stt.STT):
    def __init__(self, text: str | None) -> None:
        super().__init__(capabilities=lk_stt.STTCapabilities(streaming=False, interim_results=False))
        self._text = text
        self.closed = False

    @property
    def model(self) -> str:
        return "fake-model"

    @property
    def provider(self) -> str:
        return "fake"

    async def _recognize_impl(
        self, buffer: object, *, language: object = None, conn_options: APIConnectOptions | None = None
    ) -> lk_stt.SpeechEvent:
        alternatives = (
            [] if self._text is None else [lk_stt.SpeechData(language="en", text=self._text)]  # type: ignore[arg-type]
        )
        return lk_stt.SpeechEvent(type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=alternatives)

    async def aclose(self) -> None:
        self.closed = True


async def _recognize(text: str | None) -> lk_stt.SpeechEvent:
    wrapper = NonSpeechFilterSTT(_FakeSTT(text))
    return await wrapper.recognize([])  # type: ignore[arg-type]


async def test_an_annotation_only_result_has_no_alternatives_so_the_adapter_drops_it() -> None:
    """`StreamAdapter` emits nothing for an event with no alternatives: silence stays silence."""
    event = await _recognize("[BLANK_AUDIO]")
    assert event.alternatives == []


async def test_speech_passes_through_with_the_tag_removed() -> None:
    event = await _recognize("what is the late fee [LAUGHTER]")
    assert [a.text for a in event.alternatives] == ["what is the late fee"]


async def test_a_result_with_no_alternatives_passes_through() -> None:
    assert (await _recognize(None)).alternatives == []


async def test_the_wrapper_reports_the_inner_model_and_closes_it() -> None:
    inner = _FakeSTT("hi")
    wrapper = NonSpeechFilterSTT(inner)
    assert wrapper.model == "fake-model" and wrapper.provider == "fake"
    await wrapper.aclose()
    assert inner.closed is True
