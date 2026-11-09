"""T-OBS-01 · OBS-R1/R2: PII redaction (regex pre-pass + Presidio), known values, fail-closed."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from supportagent.observability import redaction
from supportagent.observability.redaction import Redacted, redact, redact_mapping

SAMPLES = yaml.safe_load((Path(__file__).parent / "fixtures" / "pii_samples.yaml").read_text())


@pytest.mark.parametrize("case", SAMPLES["redact"], ids=lambda c: c["expected"][:40])
def test_sample_is_redacted(case: dict[str, str]) -> None:
    result = redact(case["text"])
    assert result.text == case["expected"]
    assert result.fallback is False


@pytest.mark.parametrize("text", SAMPLES["keep"])
def test_amounts_dates_and_names_we_own_are_kept(text: str) -> None:
    result = redact(text)
    assert result.text == text
    assert result.entities == []


def test_entities_lists_the_placeholder_types() -> None:
    result = redact(SAMPLES["redact"][0]["text"])
    assert result.entities == ["DOB", "LOAN_NUMBER", "PERSON", "PHONE"]


def test_known_values_are_redacted_case_insensitively_with_possessives() -> None:
    known = ["Frank Miller", "1985-03-14"]
    result = redact("FRANK MILLER here. Frank's wife said frank miller's DOB is 1985-03-14.", known=known)
    assert result.text == "<PERSON> here. <PERSON>'s wife said <PERSON>'s DOB is <DOB>."
    assert "frank" not in result.text.lower()


def test_known_date_is_redacted_in_spoken_forms() -> None:
    result = redact("it is March 14, 1985 and 14 March 1985 and 03/14/1985", known=["1985-03-14"])
    assert result.text == "it is <DOB> and <DOB> and <DOB>"


def test_known_value_beats_a_missed_ner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redaction, "_presidio_pass", lambda text: (text, []))
    result = redact("call Zebedee Quorn tomorrow", known=["Zebedee Quorn"])
    assert result.text == "call <PERSON> tomorrow"


def test_spoken_digits_need_four_words() -> None:
    assert redact("one two three").text == "one two three"
    assert redact("one two three four").text == "<NUMBER>"


def test_redact_mapping_redacts_nested_strings_and_keeps_other_values() -> None:
    obj: dict[str, Any] = {
        "phone": "+15550100302",
        "nested": {"notes": ["call 555-010-0302", 42, None, {"x": "loan 5520264417"}]},
        "amount": 412.5,
        "ok": True,
        "label": "due on November 12",
    }
    out = redact_mapping(obj)
    assert out == {
        "phone": "<PHONE>",
        "nested": {"notes": ["call <PHONE>", 42, None, {"x": "loan <LOAN_NUMBER>"}]},
        "amount": 412.5,
        "ok": True,
        "label": "due on November 12",
    }
    assert obj["phone"] == "+15550100302"  # not mutated


def test_redact_mapping_handles_tuples_and_plain_strings() -> None:
    assert redact_mapping(("a@example.com", 1)) == ("<EMAIL>", 1)
    assert redact_mapping("a@example.com") == "<EMAIL>"
    assert redact_mapping(7) == 7


def test_presidio_failure_falls_back_to_regex_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Broken:
        def analyze(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("boom")

    monkeypatch.setattr(redaction, "_analyzer", lambda: Broken())
    text = "I'm Frank Miller, loan 4401 2287 3310, phone 555-010-0302, born March 14 1985"
    result = redact(text)
    assert result.fallback is True
    for raw in ("4401", "2287", "3310", "555-010-0302", "1985"):
        assert raw not in result.text
    assert "<PHONE>" in result.text and "<LOAN_NUMBER>" in result.text and "<DOB>" in result.text
    assert any("presidio" in r.getMessage().lower() for r in caplog.records)
    assert all("555-010-0302" not in r.getMessage() for r in caplog.records)


def test_empty_text_and_return_type() -> None:
    result = redact("")
    assert result == Redacted(text="", entities=[], fallback=False)


def test_warmup_loads_the_engines_once() -> None:
    redaction.warmup()
    assert redaction._analyzer() is redaction._analyzer()
    assert redaction._anonymizer() is redaction._anonymizer()
