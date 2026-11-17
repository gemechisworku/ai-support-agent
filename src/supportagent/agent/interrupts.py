"""Global interrupt intents (AGENT-R3): a deterministic detector that runs on every caller turn.

Some things a caller says override whatever state the call is in, and must never depend on the model
noticing: a request for a person, a dispute, "stop calling me", a lawyer, hardship, a complaint, someone
else answering. `detect_interrupt(text)` looks for them with phrase and regex tables *before* the LLM sees
the turn; the shared `report_concern(kind)` tool is the model-side catch-all for what the tables miss.

::

    detect_interrupt("Stop calling me")                                   # 'stop_calling'
    detect_interrupt("I don't want to dispute anything, just pay")        # None
    detect_interrupts("I dispute this and I lost my job")                 # ['dispute', 'hardship']

How a phrase is judged:

- The text is lower-cased, curly apostrophes and "dont"-style spellings are normalised, and it is split into
  clauses at punctuation and at "but" / "so" / "though" / "anyway", so a negation never reaches across a
  clause ("I don't want to wait, get me a human" still asks for a human).
- A match is **negated** when a negator ("don't", "not", "never", "without", "no" ...) directly precedes it,
  allowing a few filler words ("I don't *want to* dispute", "no *need for a* lawyer"). Negated matches are
  ignored. "I'm not happy, I want to complain" is not negated: "happy" is not a filler word.
- When several kinds match, `detect_interrupt` returns the most serious by `PRIORITY`: a cease request beats
  everything, then legal, dispute, hardship, complaint, a request for a person, and third party.

Detection is conservative about *cease* wording: "stop calling me" (even "at work") counts; "don't call me
at work, call after six" is a preferred calling window, not a cease request, and does not.

`screen_turn(state, text)` is the per-turn entry point used by `BaseStateAgent.on_user_turn_completed`.
It updates the call's intents and the misunderstanding counter and returns the concern to act on, if any.
Acting on it (the handlers in the plan's interrupt table) is `handle_concern`, below.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, Literal, get_args

from supportagent.common import clock
from supportagent.common.logging import get_logger
from supportagent.contracts.gateway import EscalationReason
from supportagent.contracts.outcome import ContactResult, Intent

if TYPE_CHECKING:
    from supportagent.agent.base import BaseStateAgent, ConcernKind
    from supportagent.agent.context import CallState

log = get_logger(__name__)

InterruptKind = Literal[
    "human_requested",
    "dispute",
    "stop_calling",
    "third_party",
    "legal",
    "complaint",
    "hardship",
]
"""What the phrase tables detect. `report_concern` also accepts `misunderstanding` (a counter)."""

PRIORITY: Final[tuple[InterruptKind, ...]] = (
    "stop_calling",
    "legal",
    "dispute",
    "hardship",
    "complaint",
    "human_requested",
    "third_party",
)
assert set(PRIORITY) == set(get_args(InterruptKind))

INTENT_FOR: Final[dict[str, Intent]] = {
    "human_requested": Intent.human_request,
    "dispute": Intent.dispute,
    "stop_calling": Intent.cease_contact,
    "legal": Intent.legal,
    "complaint": Intent.complaint,
    "hardship": Intent.hardship,
}
"""The outcome `intent[]` each kind records. `third_party` is a contact result, not an intent."""

MISUNDERSTANDING_LIMIT: Final = 2
"""Consecutive turns the caller could not follow (or the model reported) before the call goes to a person."""

# --------------------------------------------------------------------------- normalisation

_APOSTROPHES = {
    0x2019: "'",
    0x2018: "'",
    0x60: "'",
}  # curly quotes and a backtick, as ASR/keyboards produce them
_CONTRACTIONS = re.compile(r"\b(don|doesn|didn|isn|aren|wasn|won|can|couldn|haven|hasn)t\b")
_CLAUSE_SPLIT = re.compile(r"[.,;:!?\n]+|\s+(?:but|so|though|anyway)\s+")
_SPACES = re.compile(r"\s+")


def _normalise(text: str) -> str:
    t = text.lower().translate(_APOSTROPHES)
    t = _CONTRACTIONS.sub(lambda m: f"{m.group(1)}'t", t)
    return t


def _clauses(text: str) -> list[str]:
    parts = (_SPACES.sub(" ", p).strip() for p in _CLAUSE_SPLIT.split(_normalise(text)))
    return [p for p in parts if p]


# --------------------------------------------------------------------------- negation

_FILLER = (
    r"(?:really|even|actually|currently|going|want|wanna|need|have|got|like|wish|plan|trying|try|think|"
    r"believe|about|ever|any|an?|the|my|your|you|i|to|be|get|having|talk|speak|with|anyone|anything|it|this|that)"
)
_NEGATED = re.compile(
    r"\b(?:don't|do not|doesn't|didn't|did not|never|not|without|won't|isn't|aren't|wasn't|haven't|"
    rf"hasn't|cannot)\s+(?:{_FILLER}\s+){{0,4}}$"
)
_NO_PREFIX = re.compile(r"\bno\s+(?:(?:need|more|a|an|any|further|real|longer)\s+){0,2}$")


def _negated(prefix: str) -> bool:
    return bool(_NEGATED.search(prefix) or _NO_PREFIX.search(prefix))


# --------------------------------------------------------------------------- phrase tables

_PERSON = r"(?:human|person|people|agent|representative|rep|manager|supervisor|operator)"
_QUALIFIER = r"(?:real\s+|live\s+|actual\s+|human\s+)?"
_ARTICLE = r"(?:a\s+|an\s+|the\s+|your\s+|one\s+of\s+your\s+)?"
_BRANCH = r"(?!\s+(?:at|in|from)\s+(?:the|my|your|a)\s+(?:branch|bank|office|work|home|store)\b)"
_CEASE_WINDOW = (
    r"(?!\s+(?:at|during|before|after|between|on|in|until|unless|tomorrow|today|tonight|this|next|so|"
    r"early|late|for)\b)"
)


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


PATTERNS: Final[dict[InterruptKind, tuple[re.Pattern[str], ...]]] = {
    "human_requested": _compile(
        rf"\b(?:talk|speak|chat)\s+(?:to|with)\s+{_ARTICLE}{_QUALIFIER}(?:{_PERSON}|someone|somebody)\b{_BRANCH}",
        rf"\b(?:get|put|transfer|connect|pass|patch|bring|give)\s+(?:me|us)\s+"
        rf"(?:over\s+to\s+|through\s+to\s+|to\s+|with\s+)?{_ARTICLE}{_QUALIFIER}{_PERSON}\b",
        rf"\b(?:want|need|would\s+like|'d\s+like|d\s+like|wanna|require|prefer)\s+"
        rf"(?:to\s+(?:talk|speak)\s+(?:to|with)\s+)?{_ARTICLE}{_QUALIFIER}{_PERSON}\b",
        rf"^(?:a\s+|an\s+)?{_QUALIFIER}{_PERSON}(?:\s+please)?$",
        rf"\bis\s+there\s+(?:a\s+|an\s+)?{_QUALIFIER}{_PERSON}\b",
    ),
    "dispute": _compile(
        r"\bdisput(?:e|es|ed|ing)\b",
        r"\b(?:not|isn't|wasn't)\s+(?:my|mine)\s+(?:charge|debt|loan|account|payment|fee|balance)\b",
        r"\bi\s+(?:don't|do not|never)\s+"
        r"(?:owe|borrowed|took\s+out|opened|agreed\s+to|signed\s+up\s+for|signed)\b",
        r"\b(?:this|that|it)(?:\s+is|'s|\s+was)\s+(?:a\s+)?(?:fraud|fraudulent|scam)\b",
        r"\b(?:identity\s+theft|fraudulent|unauthori[sz]ed\s+(?:charge|loan|transaction))\b",
        r"\b(?:charge|fee|amount|balance|debt|loan|payment)\s+(?:is|was|looks|seems)\s+"
        r"(?:wrong|incorrect|a\s+mistake|not\s+right|mistaken|too\s+high)\b",
        r"\b(?:wrong|incorrect|mistaken)\s+(?:charge|fee|amount|balance)\b",
        r"\b(?:charged|billed)\s+(?:me\s+)?(?:twice|two\s+times|double)\b|\bdouble[- ](?:charged|billed)\b",
    ),
    "stop_calling": _compile(
        r"\b(?:stop|quit|cease)\s+(?:calling|phoning|contacting|ringing|harassing|bothering|texting|emailing)\b",
        rf"\b(?:don't|do not|never|no\s+longer)\s+(?:call|phone|contact|ring|text|bother)\s+"
        rf"(?:me|us|this\s+number|my\s+number)\b{_CEASE_WINDOW}",
        r"\b(?:take|remove|delete|erase|get)\s+(?:me|my\s+(?:number|name|phone(?:\s+number)?|details))\s+"
        r"(?:off|from|out\s+of)\s+(?:your|the|this)\s+(?:\w+\s+){0,2}(?:list|lists|database|system|records)\b",
        r"\bremove\s+my\s+(?:phone\s+)?number\b",
        r"\bdo[- ]not[- ]call(?:\s+(?:list|registry))\b|\bdo-not-call\b",
        r"\bno\s+more\s+(?:phone\s+)?calls\b",
        r"\b(?:leave|let)\s+me\s+(?:alone|be)\b",
        r"\bcease\s+(?:and\s+desist|contact|communication)\b",
        rf"\bi\s+(?:don't|do not)\s+want\s+(?:you|anyone|any\s+of\s+you)(?:\s+guys)?\s+(?:to\s+)?"
        rf"(?:call|calling|contact|contacting|phone|phoning)\b{_CEASE_WINDOW}",
    ),
    "third_party": _compile(
        r"\b(?:this\s+is|i'm|i\s+am|it's)\s+(?:his|her|their|the)\s+(?:wife|husband|spouse|partner|son|"
        r"daughter|mother|mom|father|dad|brother|sister|roommate|friend|assistant|neighbou?r|cousin|"
        r"parent|child|kid|boss)\b",
        r"\b(?:he|she|they)(?:'s|\s+is|\s+isn't|\s+is\s+not|\s+aren't|\s+are\s+not)\s+(?:not\s+)?"
        r"(?:here|home|available|around|out|asleep|sleeping|at\s+work|busy|away|abroad|traveling|travelling)\b",
        r"\b(?:doesn't|does\s+not|no\s+longer)\s+live\s+here\b",
        r"\b(?:take|leave)\s+a\s+message\b",
        r"\bwho(?:'s|\s+is)\s+(?:this|calling)\s+for\b",
        r"\bon\s+(?:his|her|their)\s+behalf\b",
        r"\b(?:speaking|talking)\s+(?:to|with)\s+(?:his|her)\s+\w+\b",
        r"\b(?:this|that)\s+(?:isn't|is\s+not|ain't)\s+(?:him|her)\b",
    ),
    "legal": _compile(
        r"\b(?:lawyer|lawyers|attorney|attorneys|solicitor|legal\s+action|sue|suing|lawsuit|bankruptcy|"
        r"bankrupt|court)\b",
        r"\bchapter\s+(?:7|11|13)\b",
    ),
    "complaint": _compile(
        r"\bcomplain(?:t|ts|ing)?\b",
        r"\b(?:unacceptable|ridiculous|outrageous|disgraceful|terrible|awful|horrible)\s+"
        r"(?:service|treatment|behavio[u]?r|company|bank)\b",
        r"\b(?:service|treatment)\s+(?:is\s+|was\s+)?(?:terrible|awful|horrible|unacceptable|a\s+joke|"
        r"a\s+disgrace|outrageous)\b",
        r"\b(?:this\s+is|you're|you\s+are)\s+(?:so\s+|totally\s+)?(?:unacceptable|a\s+joke|a\s+disgrace|"
        r"outrageous|ridiculous)\b",
        r"\b(?:ombudsman|regulator|cfpb|consumer\s+protection|better\s+business\s+bureau)\b",
        r"\b(?:very|really|so|extremely)\s+(?:unhappy|upset|angry|furious|dissatisfied|disappointed)\s+"
        r"(?:with|about)\b",
    ),
    "hardship": _compile(
        r"\bhardship\b",
        r"\b(?:lost|lose|losing)\s+(?:my|our)\s+(?:job|work|income|house|home)\b",
        r"\b(?:laid\s+off|got\s+fired|was\s+fired|got\s+let\s+go|been\s+let\s+go|unemployed|out\s+of\s+work|"
        r"out\s+of\s+a\s+job|between\s+jobs|no\s+income|no\s+job)\b",
        r"\b(?:can't|cannot|can\s+not|couldn't|unable\s+to)\s+(?:afford|make\s+ends\s+meet|keep\s+up|cope)\b",
        r"\b(?:medical\s+(?:bills|emergency|expenses|issues)|in\s+(?:the\s+)?hospital|had\s+surgery|"
        r"passed\s+away|funeral|death\s+in\s+(?:the|my)\s+family|diagnosed)\b",
        r"\b(?:struggling|hard\s+time)\s+(?:financially|to\s+pay|paying|with\s+money|with\s+(?:my\s+)?"
        r"(?:bills|payments|rent))\b",
        r"\b(?:reduced|cut)\s+(?:hours|pay|income|salary|wages)\b|"
        r"\b(?:hours|pay|income|salary|wages)\s+(?:were|was|got|have\s+been|has\s+been)\s+(?:cut|reduced|slashed)\b",
        r"\bfinancial\s+(?:difficulty|difficulties|trouble|problems)\b|\bmoney\s+(?:problems|trouble)\b",
    ),
}

_MISUNDERSTANDING = _compile(
    r"\bi\s+(?:don't|do not|didn't|did not)\s+(?:understand|get\s+(?:it|that|what\s+you))\b",
    r"\bwhat\s+do\s+you\s+mean\b",
    r"\bi\s+(?:don't|do not)\s+know\s+what\s+you\s+mean\b",
    r"^(?:huh|what|pardon|sorry|come\s+again|excuse\s+me|eh)$",
    r"\b(?:can|could|would)\s+you\s+(?:please\s+)?(?:repeat|say)\b",
    r"\b(?:say|repeat)\s+(?:that|it)\s+(?:again|one\s+more\s+time)\b|\brepeat\s+that\b",
    r"\bi\s+(?:didn't|did\s+not|couldn't|could\s+not|can't)\s+(?:catch|hear|follow)\b",
    r"\bi(?:'m|\s+am)\s+(?:confused|lost)\b",
    r"\b(?:doesn't|does\s+not|don't)\s+make\s+(?:any\s+)?sense\b|\bnot\s+making\s+sense\b",
)


# --------------------------------------------------------------------------- detection


def _matches(patterns: tuple[re.Pattern[str], ...], clause: str) -> bool:
    for pattern in patterns:
        for m in pattern.finditer(clause):
            if not _negated(clause[: m.start()]):
                return True
    return False


def detect_interrupts(text: str) -> list[InterruptKind]:
    """Every interrupt kind found in `text`, most serious first (`PRIORITY`), each at most once."""
    clauses = _clauses(text)
    if not clauses:
        return []
    found = {kind for kind in PRIORITY if any(_matches(PATTERNS[kind], clause) for clause in clauses)}
    return [kind for kind in PRIORITY if kind in found]


def detect_interrupt(text: str) -> InterruptKind | None:
    """The most serious interrupt in `text`, or None (AGENT-R3). Pure, no I/O, well under a millisecond."""
    kinds = detect_interrupts(text)
    return kinds[0] if kinds else None


def is_misunderstanding(text: str) -> bool:
    """The caller says they could not follow ("I don't understand", "what?", "can you repeat that")."""
    return any(any(p.search(clause) for p in _MISUNDERSTANDING) for clause in _clauses(text))


# --------------------------------------------------------------------------- per-turn screening


def note_misunderstanding(state: CallState) -> bool:
    """Count one misunderstanding for the current turn (at most once per turn); True at the limit.

    The counter is *consecutive*: `screen_turn` resets it at the start of any turn that follows a turn
    without one. The model's `report_concern("misunderstanding")` goes through here too, so a turn the
    detector already counted is not counted twice.
    """
    if not state.misunderstood_this_turn:
        state.misunderstandings += 1
        state.misunderstood_this_turn = True
    return state.misunderstandings >= MISUNDERSTANDING_LIMIT


def record_kind(state: CallState, kind: str) -> None:
    """Record `kind`'s outcome intent on the call (once)."""
    intent = INTENT_FOR.get(kind)
    if intent is not None:
        state.add_intent(intent)


def screen_turn(state: CallState, text: str) -> ConcernKind | None:
    """Look at one caller turn before the model does. Returns the concern to act on, or None.

    Records every interrupt found as an outcome intent, returns the most serious one, and counts
    consecutive misunderstandings (returning `"misunderstanding"` once the limit is reached).
    """
    previous_turn_misunderstood = state.misunderstood_this_turn
    state.misunderstood_this_turn = False
    if not previous_turn_misunderstood:
        state.misunderstandings = 0

    kinds = detect_interrupts(text)
    if kinds:
        state.misunderstandings = 0
        for kind in kinds:
            record_kind(state, kind)
        return kinds[0]
    if is_misunderstanding(text) and note_misunderstanding(state):
        return "misunderstanding"
    return None


# --------------------------------------------------------------------------- handlers

ESCALATION: Final = "escalation"
THIRD_PARTY: Final = "third_party"
CLOSING: Final = "closing"
"""Registry names of the states the handlers hand off to (see the plan's "Contract with later states")."""

CEASE_CONFIRMATION: Final = (
    "Understood. I've noted that you don't want to be contacted again, and we will respect that. Goodbye."
)

_ESCALATION_REASON: Final[dict[str, EscalationReason]] = {
    "human_requested": EscalationReason.human_requested,
    "complaint": EscalationReason.complaint,
    "legal": EscalationReason.legal,
    "hardship": EscalationReason.hardship,
    "dispute": EscalationReason.dispute,
    "misunderstanding": EscalationReason.misunderstanding,
}


def _target(agent: BaseStateAgent, name: str, **kwargs: object) -> BaseStateAgent | None:
    """The next state for `name`, or None when this *is* that state or it is not registered yet."""
    from supportagent.agent.base import STATE_REGISTRY

    if agent.state_name == name:
        return None  # already there: do not hand off to ourselves in a loop
    cls = STATE_REGISTRY.get(name)
    if cls is None:
        log.error("interrupt handler state is not registered", extra={"target": name})
        return None
    return agent.next_state(cls, **kwargs)


async def _set_flag(agent: BaseStateAgent, flag: Literal["dispute", "cease_contact"]) -> None:
    result = await agent.state.gateway.set_contact_flag(flag, note="raised by the caller during the call")
    if not result.ok:
        code = result.error.code if result.error else "unknown"
        log.warning("contact flag was not applied", extra={"flag": flag, "code": code})


async def _request_cease_ticket(agent: BaseStateAgent) -> None:
    result = await agent.state.gateway.request_callback(preferred_at=clock.now(), reason="cease request")
    if not result.ok:
        code = result.error.code if result.error else "unknown"
        log.warning("cease ticket was not created", extra={"code": code})


async def handle_concern(agent: BaseStateAgent, kind: ConcernKind) -> BaseStateAgent | None:
    """Apply the handler for `kind` (the plan's interrupt table) and return the state to hand off to.

    Returns None when the call stays in the current state (it already is the target, or the target state
    is not registered); the flags below are still recorded. Flags are set once per call.
    """
    state = agent.state
    record_kind(state, kind)
    log.info("interrupt", extra={"state": agent.state_name, "kind": kind, "verified": state.verified})

    if kind == "stop_calling":
        first = not state.cease_contact_flag
        state.cease_contact_flag = True  # the outcome says so even if a gateway call below fails
        if first:
            if state.verified:
                await _set_flag(agent, "cease_contact")
            else:
                await _request_cease_ticket(agent)
        # Queued, not awaited: this runs inside the reply that is being generated for this very turn, and
        # waiting for the new speech to play out would wait on itself. The hand-off and `shutdown(drain=True)`
        # both let queued speech finish first, so the confirmation is spoken before the next state speaks.
        agent.session.say(CEASE_CONFIRMATION, allow_interruptions=False)
        closing = _target(agent, CLOSING)
        if closing is None and agent.state_name != CLOSING:
            agent.session.shutdown(drain=True)  # no closing state to end the call: end it here
        return closing

    if kind == "dispute":
        first = not state.dispute_flag
        state.dispute_flag = True
        if first and state.verified:  # unverified: the gateway would refuse; the escalation carries it
            await _set_flag(agent, "dispute")
        return _target(agent, ESCALATION, reason=EscalationReason.dispute)

    if kind == "third_party":
        if not state.verified:  # same effect as the greeting's `third_party_answered` tool
            state.contact_result = ContactResult.third_party
        return _target(agent, THIRD_PARTY)

    return _target(agent, ESCALATION, reason=_ESCALATION_REASON[kind])
