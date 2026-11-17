"""`GreetingAgent`: the first state of every call (AGENT-R2, AGENT-R4).

On entry, in code: say the AI and recording disclosure verbatim from the registry (the first utterance of
every call), then
- outbound: `lookup_by_phone` and ask "May I speak with {first_name}?" (no name when there is no match);
- inbound: hand straight to the router. No name is spoken: nothing is verified yet.

The model only classifies the answer, with three tools; the transitions are code:
`confirm_right_party` → `VerifyAgent`, `third_party_answered` → `ThirdPartyAgent`,
`wrong_number` → record the result and end the call.
"""

from __future__ import annotations

from livekit.agents import Agent, RunContext, function_tool

from supportagent.agent.base import BaseStateAgent, register_state
from supportagent.agent.context import CallState
from supportagent.agent.disclose import speak_disclosure
from supportagent.agent.states import flow
from supportagent.agent.states.third_party import ThirdPartyAgent
from supportagent.agent.states.verify import VerifyAgent
from supportagent.common.logging import get_logger
from supportagent.contracts.outcome import ContactResult, Direction

log = get_logger(__name__)

ASK_FOR_ACCOUNT_HOLDER = "May I speak with the account holder?"


def ask_for_first_name(first_name: str) -> str:
    return f"May I speak with {first_name}?"


@register_state
class GreetingAgent(BaseStateAgent):
    state_name = flow.GREETING

    async def on_state_enter(self) -> None:
        spoken = speak_disclosure(self, "ai_recording")  # always first, verbatim, recorded with its version
        state = self.state
        if state.metadata.direction is Direction.inbound:
            # `session.update_agent` does nothing while the session is still starting (we are in on_enter),
            # so move on once the disclosure has been spoken.
            await spoken
            self._to_router()
            return
        looked_up = await state.gateway.lookup_by_phone()
        data = looked_up.data if looked_up.ok else None
        if data is not None and data.match and data.first_name:
            state.callee_first_name = data.first_name
            self.session.say(ask_for_first_name(data.first_name))
        else:
            self.session.say(ASK_FOR_ACCOUNT_HOLDER)

    def _to_router(self) -> None:
        router = flow.resolve(flow.ROUTER)
        if router is None:  # T-AGENT-05 has not landed: stay here and let the caller speak
            log.warning("no router state registered", extra={"state": self.state_name})
            return
        self.session.update_agent(self.next_state(router))

    @function_tool
    async def confirm_right_party(self, context: RunContext[CallState]) -> Agent:
        """The person on the line says they are the person we are calling. Then they verify."""
        context.userdata.contact_result = ContactResult.right_party
        return self.next_state(VerifyAgent)

    @function_tool
    async def third_party_answered(self, context: RunContext[CallState]) -> Agent:
        """Someone else answered: a relative, a colleague, or the person is not available."""
        context.userdata.contact_result = ContactResult.third_party
        return self.next_state(ThirdPartyAgent)

    @function_tool
    async def wrong_number(self, context: RunContext[CallState]) -> str:
        """The person says we have the wrong number, or that nobody by that name lives or works here."""
        context.userdata.contact_result = ContactResult.wrong_number
        flow.end_call(context)
        return "Apologise briefly for the mistake, say nothing about why we called, and say goodbye."
