"""`ThirdPartyAgent`: someone other than the customer answered (AGENT-R2, AGENT-R4).

On entry, in code: the registry's neutral message, verbatim (`third_party_neutral`: who is calling, for whom,
please call back, nothing else). The model may then only note a good time to call back and end the call. It
has no data tools, and the gateway has not verified anyone, so no amount, date, product or loan reference
can be spoken. The outcome records `contact_result=third_party`.
"""

from __future__ import annotations

from livekit.agents import RunContext, function_tool

from supportagent.agent.base import BaseStateAgent, register_state
from supportagent.agent.context import CallState
from supportagent.agent.disclose import speak_disclosure
from supportagent.agent.states import flow

MAX_NOTE_CHARS = 200


@register_state
class ThirdPartyAgent(BaseStateAgent):
    state_name = flow.THIRD_PARTY

    async def on_state_enter(self) -> None:
        state = self.state
        speak_disclosure(
            self, "third_party_neutral", first_name=state.callee_first_name or "the account holder"
        )

    @function_tool
    async def record_callback_time(self, context: RunContext[CallState], note: str) -> str:
        """Note when the person says the account holder can best be reached. Share nothing else.

        Args:
            note: Their words about a good time to call back, for example "after six tomorrow".
        """
        context.userdata.third_party_callback_note = note.strip()[:MAX_NOTE_CHARS] or None
        return "Noted. Thank them and say goodbye. Do not share any detail about why we called."

    @function_tool
    async def end_call(self, context: RunContext[CallState]) -> str:
        """End the call after thanking the person."""
        flow.end_call(context)
        return "Thank them politely and say goodbye in one short sentence."
