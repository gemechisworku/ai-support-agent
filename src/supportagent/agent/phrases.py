"""Two deterministic phrase checks on a caller turn, run in code before the model replies (AGENT-R1).

Both are pure (no I/O) and conservative: a miss falls back to the model's own tool call, a false hit would
move the call, so each pattern is narrow.

- `is_account_question(text)`: the caller asks about *their own* money: a balance, what they owe, an amount or
  date due, whether a payment arrived. Asked inside the policy state, this must go to the router/verification,
  never be answered from policy. A policy question that merely mentions a loan ("what is the late fee on my
  personal loan") is not one.
- `is_policy_question(text)` and `has_router_intent(text)`: does a caller turn say what they want at all (a
  policy question, an account question, a request for a person, a farewell, or one of the global interrupts)?
  A greeting or filler ("Yeah, I'm going to see.") does not, and the router asks a clarifying question in code
  instead of letting the model guess a route (second real call, 2026-11-18).
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

_POLICY: Final = re.compile(
    r"\b("
    r"fees?|late\s+charges?|charges?|penalt(y|ies)|grace(\s+period)?|interest|apr|rates?|"
    r"pay(ing)?\s+(it\s+|my\s+\w+\s+|the\s+\w+\s+)?(early|off|online|by\s+\w+)|early\s+(repayment|payment|payoff)|"
    r"prepay(ment)?|pay\s*off|payoff|"
    r"payment\s+(options?|methods?|ways?|channels?|plans?|deadlines?)|ways?\s+to\s+pay|how\s+(do|can)\s+i\s+pay|"
    r"what\s+happens\s+(if|when)|miss(ed|ing)?\s+(a\s+|an\s+|my\s+)?(payment|instal+ments?)|"
    r"policy|policies|rules?|extensions?|defer(ral|ment)?|reschedul\w*|refinanc\w*|credit\s+(score|report)|"
    r"auto\s*-?pay|direct\s+debit|how\s+does\s+\w+(\s+\w+)?\s+work"
    r")\b",
    re.IGNORECASE,
)
# Looser than `_ACCOUNT` (which moves the call): enough to know the caller is talking about their own loan, so
# the router lets the model pick the route instead of asking again.
_ACCOUNT_LOOSE: Final = re.compile(
    r"\bmy\s+(\w+\s+)?(loan|loans|account|payments?|instal+ments?|balance|statement|bill|debt|due|overdue|arrears|"
    r"credit\s+card|mortgage)\b|\b(owe|owed|overdue|past\s+due|behind\s+on)\b",
    re.IGNORECASE,
)
# A looser "I'm done" than `_DONE_PHRASES`: enough to see the caller said something, not to end the call.
_DONE_LOOSE: Final = re.compile(
    r"\b(finished|we'?re\s+through|all\s+good|all\s+sorted|nothing\s+(else|more)|no\s+more)\b", re.IGNORECASE
)
_HUMAN: Final = re.compile(
    r"\b(speak|talk|transfer|connect|put)\b.{0,25}\b(person|human|someone|somebody|agent|representative|"
    r"colleague|staff|manager|supervisor|operator|member\s+of\s+staff)\b",
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


_CAPABILITIES: Final = re.compile(
    r"\bcapabilit(y|ies)\b|\bwho\s+are\s+you\b|\bwhat\s+are\s+you\b(?!\s+(doing|going|saying|talking))|"
    r"\bwhat\s+(can|could)\s+you\s+(do|help)\b(?!.*\b(about|with\s+my|on\s+my)\b)|"
    r"\bwhat\s+are\s+you\s+(able|capable)\b|\bhow\s+(can|could)\s+you\s+help\b|"
    r"\bwhat\s+(do\s+)?you\s+know\s*[.?!]*\s*$|^\s*(please\s+)?help(\s+me)?\s*[.?!]*\s*$",
    re.IGNORECASE,
)


MAX_ACK_WORDS: Final = 5  # a turn this short with no intent is an acknowledgement, not a question


def says_done_loosely(text: str) -> bool:
    """True for a looser "I'm done" ("we're finished here", "nothing else"). Enough for the model's own
    `finish_call` to close the call, not for the code to do it unprompted."""
    return _DONE_LOOSE.search((text or "").replace(_CURLY_APOSTROPHE, "'")) is not None


def is_empty_turn(text: str | None) -> bool:
    """True when a caller turn has no words in it: whitespace, punctuation, what noise leaves behind."""
    return re.search(r"\w", text or "") is None


def is_question_like(text: str) -> bool:
    """True when a turn asks for something: a question mark, a longer statement, or a recognised intent.

    A short acknowledgement ("okay, I see", "mm-hmm") is not: there is nothing new to answer.
    """
    cleaned = (text or "").strip()
    if is_empty_turn(cleaned):
        return False
    return "?" in cleaned or len(_words(cleaned)) > MAX_ACK_WORDS or has_router_intent(cleaned)


def is_capabilities_question(text: str) -> bool:
    """True when the caller asks what the agent can do or who it is ("what can you do", "capabilities")."""
    cleaned = (text or "").replace(_CURLY_APOSTROPHE, "'")
    return bool(cleaned.strip()) and _CAPABILITIES.search(cleaned) is not None


def is_policy_question(text: str) -> bool:
    """True when the turn mentions something policy answers: fees, grace periods, paying early, options."""
    return bool(text) and _POLICY.search(text.replace(_CURLY_APOSTROPHE, "'")) is not None


def has_router_intent(text: str) -> bool:
    """True when the turn says what the caller wants: policy, account, a person, a farewell or an interrupt.

    False for a greeting or filler. The router then asks a clarifying question in code. The interrupt
    detector is imported here, not at module level, so this module stays free of the agent states.
    """
    from supportagent.agent.interrupts import detect_interrupts

    cleaned = (text or "").replace(_CURLY_APOSTROPHE, "'")
    if not cleaned.strip():
        return False
    return bool(
        is_account_question(cleaned)
        or is_policy_question(cleaned)
        or is_capabilities_question(cleaned)
        or _ACCOUNT_LOOSE.search(cleaned)
        or _DONE_LOOSE.search(cleaned)
        or _HUMAN.search(cleaned)
        or is_farewell(cleaned)
        or detect_interrupts(cleaned)
    )


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
