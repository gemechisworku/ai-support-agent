"""`RouterAgent`: where an inbound call goes after the greeting (AGENT-R1, U3, U4).

On entry, in code: "How can I help?" (first time) or "Is there anything else I can help you with?" (back from
the account or policy state). The model listens to the answer and calls exactly one tool; the route is code:

- `route_policy_question` → `PolicyAgent`. Policy is general information, so no verification is needed.
- `route_account_question` → `AccountAgent` if the caller is already verified, otherwise `VerifyAgent` first
  with `CallState.after_verification = "account"`, so the caller lands in the account state once verified.
  A caller who has verified once is never asked again in the same call (`flow.account_route`).
- `request_human` → `EscalationAgent(reason="human_requested")`, with no verification.
- `finish_call` → `ClosingAgent(farewell=True)`: the caller is done. It runs only when the caller's latest
  turn really is a farewell (`flow.caller_is_done`); otherwise the model is told to ask what else they need.
  The same hand-off happens in code, before the model replies, when a short caller turn is a farewell
  (`phrases.is_farewell`); right after "Is there anything else I can help you with?" a bare "no" or "thanks"
  counts too.
- A turn that asks what the agent can do ("what are your capabilities") is answered in code
  (`flow.CAPABILITIES_LINE`), and an empty turn (noise) is ignored by every state (`BaseStateAgent._screen`).
- A reply that is exactly a phrase the prompt once quoted as a caller's words ("that's all", "no thanks") is
  never spoken (`toolcall_guard.drop_recited_fragments`); the clarifying question is said instead.

Each route records its intent in `CallState.intents` (once each, in order). The router has no data tools: it
cannot read an account, search policy or verify anyone itself. Account and policy states hand back here
(`self.next_state(RouterAgent)`) when the caller is done, so one call can switch between the two.

The hand-offs are silent: the next state speaks first, and it answers the question already in the chat
history (a successor should call `self.session.generate_reply()` from `on_state_enter`).

A route whose state is not built yet (escalation is T-AGENT-11) never promises a follow-up the system does not
arrange: `flow.unavailable_line` books a callback through the gateway and the line speaks the time the gateway
returned; if that fails, the line says plainly that it cannot be done on this call and promises nothing.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from livekit.agents import Agent, ModelSettings, RunContext, function_tool
from livekit.agents.llm import ChatContext, ChatMessage, StopResponse, Tool

from supportagent.agent.base import BaseStateAgent, register_state
from supportagent.agent.context import CallState
from supportagent.agent.phrases import has_router_intent
from supportagent.agent.states import flow
from supportagent.agent.toolcall_guard import drop_recited_fragments
from supportagent.common.logging import get_logger
from supportagent.contracts.outcome import Intent

log = get_logger(__name__)

HOW_CAN_I_HELP = "How can I help?"
CLARIFY = "I'm sorry, I didn't catch that. Is it about your own account, or about how something works?"
MAX_CLARIFICATIONS = 2  # after this many unclear turns in a row the model takes over
ANYTHING_ELSE = "Is there anything else I can help you with?"
# What a caller says, never what the router says: the model must not speak them as a reply (third real call).
RECITED = ("that's all", "that is all", "that's it", "no thanks", "no thank you")


@register_state
class RouterAgent(BaseStateAgent):
    state_name = flow.ROUTER

    @property
    def _returning(self) -> bool:
        # The state was just appended to `states_visited`; more than one entry means we came back.
        return self.state.states_visited.count(self.state_name) > 1

    async def on_state_enter(self) -> None:
        self.session.say(ANYTHING_ELSE if self._returning else HOW_CAN_I_HELP)

    async def _screen(self, message: ChatMessage) -> bool:
        """Interrupts first (AGENT-R3), then a farewell in code: the call closes without a model reply."""
        if await super()._screen(message):
            return True
        if await flow.route_on_phrases(self, message, farewell=True, asked_anything_else=self._returning):
            return True
        if await flow.speak_capabilities(self, message):  # "what can you do?": answered in code
            return True
        return self._clarify(message)

    async def llm_node(
        self, chat_ctx: ChatContext, tools: list[Tool], model_settings: ModelSettings
    ) -> AsyncGenerator[Any, None]:
        stream = super().llm_node(chat_ctx, tools, model_settings)
        async for chunk in drop_recited_fragments(stream, RECITED, lambda: self.session.say(CLARIFY)):
            yield chunk

    def _clarify(self, message: ChatMessage) -> bool:
        """A turn that says nothing about what the caller wants gets a fixed question, not a model guess.

        The model used to route "Yeah, I'm going to see." to the policy state. True when the question was
        asked (the model's reply for this turn is skipped); after `MAX_CLARIFICATIONS` in a row the model
        decides.
        """
        state = self.state
        if has_router_intent(message.text_content or ""):
            state.unclear_turns = 0
            return False
        if state.unclear_turns >= MAX_CLARIFICATIONS:
            return False
        state.unclear_turns += 1
        self.session.say(CLARIFY)
        return True

    @function_tool
    async def route_policy_question(self, context: RunContext[CallState]) -> str | Agent:
        """The caller asks how something works: fees, grace periods, early payment, payment options, rules."""
        context.userdata.record_intent(Intent.policy_question)
        return await self._go(flow.POLICY, "caller asked a policy question")

    @function_tool
    async def route_account_question(self, context: RunContext[CallState]) -> str | Agent:
        """The caller asks about their own account: a balance, an amount due, a due date, a payment."""
        # Verification first, in code. Once it succeeds the caller lands in the account state.
        next_agent = flow.account_route(self)
        if next_agent is None:
            return await self._unavailable(flow.ACCOUNT, "caller asked about their account")
        return self.hand_off(next_agent)  # type: ignore[return-value]

    @function_tool
    async def request_human(self, context: RunContext[CallState]) -> str | Agent:
        """The caller asks to speak to a person, an agent, or a colleague."""
        context.userdata.record_intent(Intent.human_request)
        return await self._go(flow.ESCALATION, "caller asked to speak to a person", reason="human_requested")

    @function_tool
    async def finish_call(self, context: RunContext[CallState]) -> str | Agent:
        """The caller says goodbye or that they need nothing more."""
        if not flow.caller_is_done(self, asked_anything_else=self._returning):
            return flow.NOT_DONE  # guarded in code: "Mm-hmm." once hung up on a caller
        return await self._go(flow.CLOSING, "caller is done", farewell=True)

    # ------------------------------------------------------------------ code-driven exit

    async def _go(self, name: str, ticket_reason: str, **kwargs: object) -> str | Agent:
        """Hand off to state `name` (`kwargs` are its constructor arguments, e.g. an escalation `reason`)."""
        target = flow.resolve(name)
        if target is None:  # that state's task has not landed yet: keep the call alive, honestly
            return await self._unavailable(name, ticket_reason)
        return self.hand_off(self.next_state(target, **kwargs))  # type: ignore[return-value]

    async def _unavailable(self, name: str, reason: str) -> str:
        """Say in code what can honestly be said when `name` is not built yet, then skip the model's reply."""
        log.warning("no state registered for route", extra={"state": self.state_name, "next": name})
        line = await flow.unavailable_line(self.state, reason)
        self.session.say(line)  # queued, not awaited: this runs inside the reply being generated
        raise StopResponse()
