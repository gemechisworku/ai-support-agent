"""Number grounding: every figure the agent says must appear in a tool result from the same call (AGENT-R5).

Pure functions over text. A "figure" is a run of digits with optional thousands commas and decimals, so
`$1,245.10` is the token `1,245.10`, `4 December 2026` is `4` and `2026`, and a loan ending `4417` is `4417`.
Tokens are compared whole: `$200` is not grounded by a source that only says `$200.72` (a rounded or
recomputed figure is not verbatim). Spelled-out numbers ("two hundred") are not figures here; the agent is
told to read the tool's text as written, and the eval harness checks the transcript.

`ungrounded_numbers` / `assert_grounded` are the test-side check (unit tests here, EVAL-03 over whole calls).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_FIGURE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """The figures in `text` as whole tokens, minus a trailing comma (`"4 Nov, 2026,"` → `{"4", "2026"}`)."""
    return {token.rstrip(",") for token in _FIGURE.findall(text)}


def ungrounded_numbers(answer: str, sources: Iterable[str]) -> list[str]:
    """Figures in `answer` that no source contains (sorted; empty means every figure is grounded)."""
    allowed: set[str] = set()
    for source in sources:
        allowed |= numbers_in(source)
    return sorted(numbers_in(answer) - allowed)


def assert_grounded(answer: str, sources: Iterable[str]) -> None:
    """Raise `AssertionError` naming the figures in `answer` that no source (tool result) contains."""
    missing = ungrounded_numbers(answer, sources)
    if missing:
        raise AssertionError(f"figures not found in any tool result: {', '.join(missing)}; said: {answer!r}")
