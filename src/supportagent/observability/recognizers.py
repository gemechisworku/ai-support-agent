"""PII patterns shared by the regex pre-pass and the Presidio registry (T-OBS-01, OBS-R1).

The compiled patterns here are the single definition of the structured PII the redactor knows about.
`redaction._regex_pass` applies them in order (always, and alone when Presidio fails); the
`EntityRecognizer` subclasses register the same patterns into Presidio so the analyzer reports them
too. Generic dates are deliberately absent: due dates must survive (NFR-ACC), so a date is a `<DOB>`
only next to a birth keyword ("born", "birthday", "date of birth", "DOB") or when it is a `known` value.
"""

import re
from typing import ClassVar, Final

from presidio_analyzer import EntityRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

MONTHS: Final = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_DAY: Final = r"\d{1,2}(?:st|nd|rd|th)?"
_YEAR: Final = r"\d{4}"

#: A calendar date in the forms people say or type. Year is optional for month-name forms ("March 14").
DATE: Final = (
    rf"(?:(?:the\s+)?{_DAY}\s+(?:of\s+)?{MONTHS},?(?:\s+{_YEAR})?"
    rf"|{MONTHS}\s+{_DAY}(?:,?\s+{_YEAR})?"
    r"|\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})"
    r"|\d{4}-\d{2}-\d{2})"
)

_SPOKEN_DIGIT: Final = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_I = re.IGNORECASE

#: (placeholder type, compiled pattern). Applied in this order by the regex pre-pass. Patterns with a
#: `keep` group keep that group's text and replace only the `pii` group.
EMAIL: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
NATIONAL_ID: Final = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
DOB_CONTEXT: Final = re.compile(
    rf"(?P<keep>\b(?:born(?:\s+on)?|birthday(?:\s+is)?|date\s+of\s+birth(?:\s+is)?|d\.?o\.?b\.?(?:\s+is)?)"
    rf"[:\s]+)(?P<pii>{DATE})",
    _I,
)
ADDRESS: Final = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s+){1,3}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd|Court|Ct|Way|Place|Pl|Terrace|Close)\b"
)
LOAN_NUMBER: Final = re.compile(
    r"(?P<keep>\bloan(?:\s+(?:number|no\.?|#|ending|ends|in|is|was))*[:\s#]*)(?P<pii>\d(?:[\s-]?\d){3,})",
    _I,
)
ACCOUNT_NUMBER: Final = re.compile(
    r"(?P<keep>\b(?:account|acct)(?:\s+(?:number|no\.?|#|ending|ends|in|is|was))*[:\s#]*)"
    r"(?P<pii>\d(?:[\s-]?\d){3,})",
    _I,
)
PHONE: Final = re.compile(r"(?<![\w+])(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
SPOKEN_DIGITS: Final = re.compile(rf"\b{_SPOKEN_DIGIT}\b(?:[\s,-]+{_SPOKEN_DIGIT}\b){{3,}}", _I)
DIGIT_RUN: Final = re.compile(r"(?<![\w$.,/-])\d(?:[ -]?\d){3,}(?![\w/])")

#: Spans the generic digit-run rule must not touch: money and calendar dates (OBS-R1 "amounts and due
#: dates are kept").
PROTECTED: Final = re.compile(
    rf"\$\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s*(?:dollars|usd|bucks|cents)\b|{DATE}",
    _I,
)

ORDERED: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("EMAIL", EMAIL),
    ("NATIONAL_ID", NATIONAL_ID),
    ("DOB", DOB_CONTEXT),
    ("ADDRESS", ADDRESS),
    ("LOAN_NUMBER", LOAN_NUMBER),
    ("ACCOUNT_NUMBER", ACCOUNT_NUMBER),
    ("PHONE", PHONE),
    ("NUMBER", SPOKEN_DIGITS),
)

#: Names the bank owns. NER sometimes tags them as people or places; they are never PII.
PRODUCT_NAMES: Final = ("Personal Loan", "Auto Loan", "Mortgage", "Salary Advance", "SME Working Capital")


class _Recognizer(EntityRecognizer):
    """Runs one compiled pattern from this module and reports only its `pii` group (or the whole match).

    Presidio's `PatternRecognizer` scores whole matches, which would put the keyword ("loan", "born")
    inside the placeholder; reporting the group span keeps the keyword readable, as the pre-pass does.
    """

    ENTITY: ClassVar[str]
    PATTERN: ClassVar[re.Pattern[str]]

    def __init__(self) -> None:
        super().__init__(supported_entities=[self.ENTITY], name=type(self).__name__, supported_language="en")

    def load(self) -> None:  # nothing to load: the pattern is compiled at import
        return None

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None
    ) -> list[RecognizerResult]:
        group = "pii" if "pii" in self.PATTERN.groupindex else 0
        return [
            RecognizerResult(self.ENTITY, m.start(group), m.end(group), 0.85)
            for m in self.PATTERN.finditer(text)
        ]


class LoanNumberRecognizer(_Recognizer):
    ENTITY = "LOAN_NUMBER"
    PATTERN = LOAN_NUMBER


class AccountNumberRecognizer(_Recognizer):
    ENTITY = "ACCOUNT_NUMBER"
    PATTERN = ACCOUNT_NUMBER


class DobContextRecognizer(_Recognizer):
    ENTITY = "DOB"
    PATTERN = DOB_CONTEXT


class SpokenDigitsRecognizer(_Recognizer):
    ENTITY = "NUMBER"
    PATTERN = SPOKEN_DIGITS


def custom_recognizers() -> list[EntityRecognizer]:
    return [
        LoanNumberRecognizer(),
        AccountNumberRecognizer(),
        DobContextRecognizer(),
        SpokenDigitsRecognizer(),
    ]
