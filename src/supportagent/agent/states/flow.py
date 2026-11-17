"""Where each state hands off to: names, the post-verification table and ending the call (AGENT-R2).

States are referred to by `state_name` and looked up in `STATE_REGISTRY` when the hand-off happens, so a
state can name a successor that a later task implements (router: T-AGENT-05, account / policy: T-AGENT-06/07,
reminder / collections / callback: T-AGENT-09/10/12, escalation: T-AGENT-11). `resolve` returns `None` for a
state that is not registered yet; callers then keep the call alive with a safe message instead of failing.

A state that is escalated to takes the keyword argument `reason` (an `EscalationReason` value), for example
`self.next_state(resolve("escalation"), reason="verification_failed")`.
"""

from __future__ import annotations

from livekit.agents import RunContext

from supportagent.agent.base import STATE_REGISTRY, BaseStateAgent
from supportagent.agent.context import CallState
from supportagent.contracts.outcome import Campaign, Direction

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
