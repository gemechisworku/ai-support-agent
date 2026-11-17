"""Two deterministic phrase checks on a caller turn, run in code before the model replies (AGENT-R1).

Both are pure (no I/O) and conservative: a miss falls back to the model's own tool call, a false hit would
move the call, so each pattern is narrow.

- `is_account_question(text)`: the caller asks about *their own* money: a balance, what they owe, an amount or
  date due, whether a payment arrived. Asked inside the policy state, this must go to the router/verification,
  never be answered from policy. A policy question that merely mentions a loan ("what is the late fee on my
  personal loan") is not one.
- `is_farewell(text, asked_anything_else=...)`: the caller is done. Short turns only (at most `MAX_WORDS`), no
  question mark, and a farewell word. A bare "no" / "thanks" counts only right after "Is there anything else I
  can help you with?".
"""

from __future__ import annotations

import re
from typing import Final

MAX_WORDS: Final = 8
_CURLY_APOSTROPHE: Final = chr(0x2019)  # speech-to-text and keyboards both produce it

_ACCOUNT: Final = re.compile(
    r"\b("
    r"(what|how much)\s+(do|did|will|would|shall)\s+(i|we)\s+(still\s+)?(owe|have\s+to\s+pay|need\s+to\s+pay)"
    r"|what\s+(is|are)\s+my\s+(balance|outstanding|arrears|overdue|payoff|payments?|instal+ments?|statement)"
    r"|my\s+(current\s+|outstanding\s+|loan\s+)?balance"
    r"|how\s+much\s+(is|are)\s+(my|the)\s+(balance|instal+ments?|payment|amount\s+due)"
    r"|(amount|payment|balance)\s+(i\s+)?(owe|due|overdue|outstanding)"
    r"|(when|what\s+date)\s+is\s+my\s+(next\s+)?(payment|instal+ment)\s+due"
    r"|my\s+(next\s+)?(due\s+date|payment\s+date)"
    r"|(did|has|have)\s+(my|the)\s+(payment|instal+ment|transfer)\s+(go|gone|arrive|arrived|come|post|posted|clear|cleared)"
    r"|my\s+(last|latest|recent)\s+(payment|payments|transaction|transactions)"
    r"|how\s+much\s+(have|had)\s+i\s+(paid|repaid)"
    r")\b",
    re.IGNORECASE,
)

_FAREWELL_WORDS: Final = re.compile(
    r"\b(good\s?bye|bye(\s+bye)?|bye-bye|see\s+you|talk\s+to\s+you\s+later|have\s+a\s+(good|nice|great)\s+(day|one))\b",
    re.IGNORECASE,
)
_DONE_PHRASES: Final = re.compile(
    r"\b("
    r"that'?s\s+(all|everything|it)|that\s+is\s+(all|everything|it)"
    r"|nothing\s+(else|more)|no\s+more\s+questions|i'?m\s+(done|good|all\s+set)|we'?re\s+(done|good)"
    r"|(i|we)\s+(have|ve|got)\s+nothing\s+else|that\s+will\s+be\s+all|that\s+would\s+be\s+all"
    r")\b",
    re.IGNORECASE,
)
# Only a farewell when the agent has just asked "anything else?": the whole turn is a short refusal or thanks.
_BARE_DONE: Final = re.compile(
    r"^\s*(no|nope|nah|no\s+thanks?|no\s+thank\s+you|nothing|not\s+really|thanks?|thank\s+you|"
    r"ok(ay)?(\s+thanks?|\s+thank\s+you)?|that'?s\s+it\s+thanks?)\s*[.!]*\s*$",
    re.IGNORECASE,
)
_NOT: Final = re.compile(r"\b(not|don'?t|do\s+not|never|wait|hold\s+on|before)\b", re.IGNORECASE)


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text)


def is_account_question(text: str) -> bool:
    """True when the caller asks about their own balance, amount owed, due date or payments."""
    return bool(text) and _ACCOUNT.search(text.replace(_CURLY_APOSTROPHE, "'")) is not None


def is_farewell(text: str, *, asked_anything_else: bool = False) -> bool:
    """True when the caller is saying goodbye or that they need nothing more."""
    cleaned = (text or "").replace(_CURLY_APOSTROPHE, "'").strip()
    if not cleaned or "?" in cleaned or len(_words(cleaned)) > MAX_WORDS:
        return False
    if asked_anything_else and _BARE_DONE.match(cleaned):
        return True
    if _NOT.search(cleaned) and not _DONE_PHRASES.search(cleaned):
        return False  # "wait, not yet", "don't hang up"
    return bool(_FAREWELL_WORDS.search(cleaned) or _DONE_PHRASES.search(cleaned))
