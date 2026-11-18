"""PII redaction: regex pre-pass, then Presidio NER, emitting `<TYPE>` placeholders (OBS-R1, OBS-R2).

1. `_regex_pass` always runs. It replaces caller-supplied `known` values, then the structured patterns
   in `recognizers.ORDERED`, then any remaining digit run of 4+ digits that is not money or a date.
2. `_presidio_pass` runs Presidio's `AnalyzerEngine` (spaCy `en_core_web_sm`) for `PERSON` and
   `LOCATION`, plus the custom recognizers, and `AnonymizerEngine` swaps spans for `<TYPE>`. Generic
   `DATE_TIME` and `NRP` are never requested, and the bank, agent and product names are allow-listed.
3. If Presidio raises, the regex-only text is returned with `fallback=True` (fail closed: the raw text
   is never returned) and a warning is logged without the text.

The engines are lazy singletons; `warmup()` loads them up front (spaCy load is ~0.5 s).
"""

import datetime as dt
import functools
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, NamedTuple

from supportagent.common import settings as cfg
from supportagent.common.logging import get_logger
from supportagent.observability import recognizers as rec

if TYPE_CHECKING:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine

log = get_logger(__name__)

NER_ENTITIES = ("PERSON", "LOCATION")
SPACY_MODEL = "en_core_web_sm"
_PLACEHOLDER = re.compile(r"<[A-Z_]+>")
_POSSESSIVE = r"(?=(?:'s)?(?!\w))"


class Redacted(NamedTuple):
    text: str
    entities: list[str]  # sorted, unique placeholder types found
    fallback: bool  # True when Presidio failed and only the regex pass ran


# ------------------------------------------------------------------ known values


def _date_variants(value: str) -> str | None:
    """A regex matching an ISO date the way it is said or typed ("March 14, 1985", "14/03", ...)."""
    try:
        d = dt.date.fromisoformat(value.strip())
    except ValueError:
        return None
    full, abbr = d.strftime("%B"), d.strftime("%b")
    month = rf"(?:{full}|{abbr}\.?)"
    day = rf"0?{d.day}(?:st|nd|rd|th)?"
    year = rf"(?:{d.year}|'?{d.year % 100:02d})"
    return (
        rf"(?:{month}\s+{day},?\s+{year}|(?:the\s+)?{day}\s+(?:of\s+)?{month},?\s+{year}"
        rf"|0?{d.month}/0?{d.day}/{year}|0?{d.day}/0?{d.month}/{year}|{d.isoformat()})"
    )


def _known_pattern(value: str) -> tuple[str, str] | None:
    value = value.strip()
    if not value:
        return None
    if date := _date_variants(value):
        return "DOB", date
    if rec.EMAIL.fullmatch(value):
        return "EMAIL", re.escape(value)
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 4 and len(digits) >= len(re.sub(r"\s", "", value)) - 2:
        kind = "PHONE" if value.startswith("+") or len(digits) in (10, 11) else "NUMBER"
        return kind, r"\+?" + r"[\s.()-]*".join(digits)
    # A name or other text: the whole value and each word of 2+ letters ("Frank Miller" → "Frank").
    words = [w for w in re.split(r"\s+", value) if len(w) >= 2]
    alts = sorted({r"\s+".join(map(re.escape, value.split())), *map(re.escape, words)}, key=len, reverse=True)
    return "PERSON", "(?:" + "|".join(alts) + ")"


def _redact_known(text: str, known: Iterable[str], found: set[str]) -> str:
    rules = [r for v in known if (r := _known_pattern(v))]
    # Longest first, so a full name or date wins over its parts.
    for kind, pattern in sorted(rules, key=lambda r: len(r[1]), reverse=True):
        regex = re.compile(rf"(?<![\w]){pattern}{_POSSESSIVE}", re.IGNORECASE)
        text, n = regex.subn(f"<{kind}>", text)
        if n:
            found.add(kind)
    return text


# ------------------------------------------------------------------ regex pre-pass


def _regex_pass(text: str, known: Iterable[str] = ()) -> tuple[str, set[str]]:
    found: set[str] = set()
    text = _redact_known(text, known, found)
    for kind, pattern in rec.ORDERED:

        def sub(m: re.Match[str], kind: str = kind) -> str:
            found.add(kind)
            if "pii" in m.re.groupindex:
                return m["keep"] + f"<{kind}>"
            return f"<{kind}>"

        text = pattern.sub(sub, text)

    protected = [m.span() for m in rec.PROTECTED.finditer(text)]

    def digits(m: re.Match[str]) -> str:
        if any(s < m.end() and m.start() < e for s, e in protected):
            return m[0]
        found.add("NUMBER")
        return "<NUMBER>"

    return rec.DIGIT_RUN.sub(digits, text), found


# ------------------------------------------------------------------ Presidio


@functools.cache
def _analyzer() -> "AnalyzerEngine":
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import SpacyRecognizer

    nlp = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": SPACY_MODEL}],
        }
    ).create_engine()
    registry = RecognizerRegistry(supported_languages=["en"])
    registry.add_recognizer(SpacyRecognizer(supported_language="en", supported_entities=list(NER_ENTITIES)))
    for recognizer in rec.custom_recognizers():
        registry.add_recognizer(recognizer)
    return AnalyzerEngine(nlp_engine=nlp, registry=registry, supported_languages=["en"])


@functools.cache
def _anonymizer() -> "AnonymizerEngine":
    from presidio_anonymizer import AnonymizerEngine

    return AnonymizerEngine()


@functools.cache
def _allow_list() -> tuple[str, ...]:
    # Only the persona group: redaction must work in processes with no database settings.
    persona = cfg.PersonaSettings(_env_file=cfg.ENV_FILE)
    names = {persona.bank_name, persona.agent_name, *persona.bank_name.split(), *rec.PRODUCT_NAMES}
    return tuple(sorted(n.lower() for n in names))


def _presidio_pass(text: str) -> tuple[str, list[str]]:
    from presidio_anonymizer.entities import OperatorConfig, RecognizerResult

    entities = list(NER_ENTITIES) + [r.supported_entities[0] for r in rec.custom_recognizers()]
    results = _analyzer().analyze(text=text, language="en", entities=entities)
    placeholders = [m.span() for m in _PLACEHOLDER.finditer(text)]
    allowed = _allow_list()
    keep = []
    for r in results:
        span = text[r.start : r.end]
        if span.strip().lower() in allowed or not span.strip(" <>,.'"):
            continue
        if any(s < r.end and r.start < e for s, e in placeholders):
            continue
        keep.append(RecognizerResult(r.entity_type, r.start, r.end, r.score))
    if not keep:
        return text, []
    out = _anonymizer().anonymize(
        text=text, analyzer_results=keep, operators={"DEFAULT": OperatorConfig("replace")}
    )
    return out.text, [r.entity_type for r in keep]


# ------------------------------------------------------------------ public API


def redact(text: str, *, known: Iterable[str] = ()) -> Redacted:
    """Return `text` with PII replaced by `<TYPE>` placeholders. Never returns the raw text on error."""
    pre, found = _regex_pass(text, known)
    if not pre.strip():
        return Redacted(pre, sorted(found), False)
    try:
        out, more = _presidio_pass(pre)
    except Exception as exc:  # fail closed: regex-only result, and never log the text
        log.warning("presidio redaction failed; regex-only fallback", extra={"error": type(exc).__name__})
        return Redacted(pre, sorted(found), True)
    return Redacted(out, sorted(found | set(more)), False)


def redact_mapping(obj: Any, *, known: Iterable[str] = ()) -> Any:
    """Redact every string inside nested dicts, lists and tuples; other values are returned unchanged."""
    known = tuple(known)
    if isinstance(obj, str):
        return redact(obj, known=known).text
    if isinstance(obj, dict):
        return {k: redact_mapping(v, known=known) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_mapping(v, known=known) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_mapping(v, known=known) for v in obj)
    return obj


def warmup() -> None:
    """Load spaCy and the Presidio engines now, so the first call turn does not pay for it."""
    _analyzer()
    _anonymizer()
    _presidio_pass("warm up")
