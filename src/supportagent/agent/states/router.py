"""`RouterAgent`: where an inbound call goes after the greeting (AGENT-R1, U3, U4).

On entry, in code: "How can I help?" (first time) or "Is there anything else I can help you with?" (back from
the account or policy state). The model listens to the answer and calls exactly one tool; the route is code:

- `route_policy_question` → `PolicyAgent`. Policy is general information, so no verification is needed.
- `route_account_question` → `AccountAgent` if the caller is already verified, otherwise `VerifyAgent` first
  with `CallState.after_verification = "account"`, so the caller lands in the account state once verified.
  A caller who has verified once is never asked again in the same call.
- `request_human` → `EscalationAgent(reason="human_requested")`, with no verification.

Each route records its intent in `CallState.intents` (once each, in order). The router has no data tools: it
cannot read an account, search policy or verify anyone itself. Account and policy states hand back here
(`self.next_state(RouterAgent)`) when the caller is done, so one call can switch between the two.

The hand-offs are silent: the next state speaks first, and it answers the question already in the chat
history (a successor should call `self.session.generate_reply()` from `on_state_enter`).
"""

from __future__ import annotations

from livekit.agents import Agent, RunContext, function_tool

from supportagent.agent.base import BaseStateAgent, register_state
from supportagent.agent.context import CallState
from supportagent.agent.states import flow
from supportagent.agent.states.verify import VerifyAgent
from supportagent.common.logging import get_logger
from supportagent.contracts.outcome import Intent

log = get_logger(__name__)

HOW_CAN_I_HELP = "How can I help?"
ANYTHING_ELSE = "Is there anything else I can help you with?"
NOT_AVAILABLE = (
    "That part is not available on this call yet. Apologise briefly and say a colleague will follow up."
)


@register_state
class RouterAgent(BaseStateAgent):
    state_name = flow.ROUTER

    async def on_state_enter(self) -> None:
        # The state was just appended to `states_visited`; more than one entry means we came back.
        returning = self.state.states_visited.count(self.state_name) > 1
        self.session.say(ANYTHING_ELSE if returning else HOW_CAN_I_HELP)

    @function_tool
    async def route_policy_question(self, context: RunContext[CallState]) -> str | Agent:
        """The caller asks how something works: fees, grace periods, early payment, payment options, rules."""
        context.userdata.record_intent(Intent.policy_question)
        return self._go(flow.POLICY)

    @function_tool
    async def route_account_question(self, context: RunContext[CallState]) -> str | Agent:
        """The caller asks about their own account: a balance, an amount due, a due date, a payment."""
        state = context.userdata
        state.record_intent(Intent.account_question)
        if state.verified:
            return self._go(flow.ACCOUNT)
        # Verification first, in code. Once it succeeds the caller lands in the account state.
        state.after_verification = flow.ACCOUNT
        return self.hand_off(self.next_state(VerifyAgent))  # type: ignore[return-value]

    @function_tool
    async def request_human(self, context: RunContext[CallState]) -> str | Agent:
        """The caller asks to speak to a person, an agent, or a colleague."""
        context.userdata.record_intent(Intent.human_request)
        return self._go(flow.ESCALATION, reason="human_requested")

    # ------------------------------------------------------------------ code-driven exit

    def _go(self, name: str, **kwargs: object) -> str | Agent:
        target = flow.resolve(name)
        if target is None:  # that state's task has not landed yet: keep the call alive
            log.warning("no state registered for route", extra={"state": self.state_name, "next": name})
            return NOT_AVAILABLE
        return self.hand_off(self.next_state(target, **kwargs))  # type: ignore[return-value]
