"""Where each state hands off to: names, the post-verification table and ending the call (AGENT-R2).

States are referred to by `state_name` and looked up in `STATE_REGISTRY` when the hand-off happens, so a
state can name a successor that a later task implements (router: T-AGENT-05, account / policy: T-AGENT-06/07,
reminder / collections / callback: T-AGENT-09/10/12, escalation: T-AGENT-11). `resolve` returns `None` for a
state that is not registered yet; callers then keep the call alive with a safe message instead of failing.

A state that is escalated to takes the keyword argument `reason` (an `EscalationReason` value), for example
`self.next_state(resolve("escalation"), reason="verification_failed")`.
"""

from __future__ import annotations

import datetime as dt

from livekit.agents import RunContext
from livekit.agents.llm import ChatMessage

from supportagent.agent.base import STATE_REGISTRY, BaseStateAgent
from supportagent.agent.context import CallState
from supportagent.agent.phrases import (
    is_account_question,
    is_capabilities_question,
    is_farewell,
    says_done_loosely,
)
from supportagent.agent.summary import format_callback
from supportagent.common import clock
from supportagent.common.logging import get_logger
from supportagent.contracts.outcome import Campaign, Direction, Intent

log = get_logger(__name__)

GREETING = "greeting"
VERIFY = "verify"
THIRD_PARTY = "third_party"
ROUTER = "router"  # inbound: "How can I help?" (T-AGENT-05)
ACCOUNT = "account"  # U3 (T-AGENT-06)
POLICY = "policy"  # U4 (T-AGENT-07)
REMINDER = "reminder"  # U1 (T-AGENT-09)
COLLECTIONS_PURPOSE = "collections_purpose"  # U2 (T-AGENT-10)
CALLBACK = "callback"  # U5 (T-AGENT-12)
ESCALATION = "escalation"  # U6 (T-AGENT-11)
CLOSING = "closing"  # read-back, callback offer, goodbye (T-AGENT-08)

_AFTER_VERIFICATION: dict[Campaign, str] = {
    Campaign.pre_due: REMINDER,
    Campaign.missed_payment: COLLECTIONS_PURPOSE,
    Campaign.callback: CALLBACK,
    Campaign.inbound: ROUTER,
    Campaign.playground: ROUTER,
}


def post_verification_state(direction: Direction, campaign: Campaign | None) -> str:
    """The state a verified caller goes to: their campaign's flow, or the router for any inbound call."""
    if direction is Direction.inbound or campaign is None:
        return ROUTER
    return _AFTER_VERIFICATION[campaign]


def resolve(name: str) -> type[BaseStateAgent] | None:
    """The registered state class for `name`, or `None` when its task has not landed yet."""
    return STATE_REGISTRY.get(name)


def end_call(context: RunContext[CallState]) -> None:
    """Close the session once the model's last reply has been spoken (`drain=True` lets it finish)."""
    context.session.shutdown(drain=True)


# ------------------------------------------------------------------ a successor that is not built yet

FOLLOW_UP_DELAY = dt.timedelta(days=1)  # a booked callback is for the next day
MAX_REASON_CHARS = 200
CALLBACK_BOOKED_LINE = (
    "I can't do that on this call, so I've arranged for a colleague to call you back on {when}. "
    "Is there anything else I can help you with?"
)
CALLBACK_DECLINED_LINE = (
    "I'm sorry, I can't do that on this call, and I couldn't arrange a call back either. "
    "Please call us again. Is there anything else I can help you with?"
)


async def unavailable_line(state: CallState, reason: str) -> str:
    """What the agent says (in code, not the model) when it must hand off to a state that is not built yet.

    A promise that someone will be in touch is only made after the gateway really created the callback
    (`request_callback`, allowed for any session), and the time spoken is the one it returned. When the
    gateway refuses or fails, the line promises nothing: it says plainly that this cannot be done here.
    """
    when = clock.now() + FOLLOW_UP_DELAY
    result = await state.gateway.request_callback(when, reason.strip()[:MAX_REASON_CHARS])
    if result.ok and result.data is not None:
        state.outcome.record_callback(result.data.due_at)
        return CALLBACK_BOOKED_LINE.format(when=format_callback(result.data.due_at))
    code = result.error.code if result.error else "unknown"
    log.warning("callback for an unavailable state not booked", extra={"error_code": code})
    return CALLBACK_DECLINED_LINE


def account_route(agent: BaseStateAgent) -> BaseStateAgent | None:
    """The state an account question goes to, in code: the account state if the caller is verified, else
    `VerifyAgent` first with `after_verification = "account"` so a verified caller lands in the account state.

    Records the `account_question` intent. Returns `None` when the needed state is not registered.
    """
    state = agent.state
    state.record_intent(Intent.account_question)
    if state.verified:
        target = resolve(ACCOUNT)
        return None if target is None else agent.next_state(target)
    verify = resolve(VERIFY)
    if verify is None:
        return None
    state.after_verification = ACCOUNT
    return agent.next_state(verify)


# --------------------------------------------------------------------------- phrase-driven exits

END_BY_CALLER = "caller_goodbye"  # CallState.end_reason when the caller said they were done
END_BY_AGENT = "completed"  # CallState.end_reason when the model ended the call through `end_call`


async def route_on_phrases(
    agent: BaseStateAgent,
    message: ChatMessage,
    *,
    farewell: bool = False,
    account: bool = False,
    asked_anything_else: bool = False,
) -> bool:
    """Move the call on a caller turn that is a farewell or an account question, in code. True when it moved.

    Runs once per caller message (a spoken turn reaches it through `on_user_turn_completed`, a typed one
    through `llm_node`). A farewell goes to `ClosingAgent(farewell=True)`, which reads back any commitments,
    says goodbye and ends the call. An account question goes to `account_route`. A target that is not
    registered leaves the call where it is (the model then answers or uses its own tool).
    """
    seen: set[str] = agent.__dict__.setdefault("_phrases_seen", set())
    if message.id in seen:
        return False
    seen.add(message.id)
    text = message.text_content or ""
    next_agent: BaseStateAgent | None = None
    if farewell and is_farewell(text, asked_anything_else=asked_anything_else):
        closing = resolve(CLOSING)
        next_agent = None if closing is None else agent.next_state(closing, farewell=True)
    elif account and is_account_question(text):
        next_agent = account_route(agent)
    if next_agent is None:
        return False
    if farewell and is_farewell(text, asked_anything_else=asked_anything_else):
        log.info(
            "farewell by phrase",
            extra={"asked_anything_else": asked_anything_else, **turn_facts(agent, text)},
        )
    agent.session.update_agent(next_agent)
    return True


# ------------------------------------------------------------------ what can you do? / are they done?

# Spoken in code when the caller asks what the agent can do. It lists only what this build really does:
# escalation to a person is not built (T-AGENT-11), so a person is not offered, only a callback request.
CAPABILITIES_LINE = (
    "I can answer questions about how things work, such as fees, grace periods and paying early. "
    "Once I've checked who you are, I can also look up your own loan, like a balance, a due date or your "
    "recent payments. And I can ask a colleague to call you back. What would you like to do?"
)

# What the model is told (it is not spoken) when it calls a finish tool but the caller has not said goodbye.
NOT_DONE = (
    "The caller has not said they are finished, so do not end the call. Ask in one short sentence what "
    "else you can help with."
)


async def speak_capabilities(agent: BaseStateAgent, message: ChatMessage) -> bool:
    """Answer "what can you do?" in code with `CAPABILITIES_LINE`. True when it did (the model is silent)."""
    if not is_capabilities_question(message.text_content or ""):
        return False
    agent.state.unclear_turns = 0
    agent.session.say(CAPABILITIES_LINE)
    return True


def turn_facts(agent: BaseStateAgent, text: str | None = None) -> dict[str, object]:
    """What a log line may say about a caller turn: its size and the router's counters, never its words."""
    said = agent.state.last_caller_text if text is None else text
    return {
        "state": agent.state_name,
        "turn_chars": len(said),
        "turn_words": len(said.split()),
        "unclear_turns": agent.state.unclear_turns,
        "states_visited": len(agent.state.states_visited),
    }


def caller_is_done(agent: BaseStateAgent, *, asked_anything_else: bool = False) -> bool:
    """Did the caller's most recent turn say goodbye or that they need nothing more? (The `finish` guard.)

    A farewell, or a looser "we're finished here". Filler ("mm-hmm"), a pause or an unclear answer is not.
    """
    text = agent.state.last_caller_text
    return is_farewell(text, asked_anything_else=asked_anything_else) or says_done_loosely(text)
