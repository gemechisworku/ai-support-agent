"""`VerifyAgent`: full name, date of birth and loan last 4, checked by `verify_identity` (AGENT-R2).

On entry, in code: ask for the three details (no name is spoken). The model's one tool collects what the
caller said; everything after that is code on the gateway's answer:

- verified → `CallState.verified = True`, `first_name`, `contact_result = right_party`, then the state the
  router asked for (`CallState.after_verification`, used once) or the one for this direction and
  campaign (`flow.post_verification_state`);
- not verified, attempts left → the model is told how many attempts remain and to ask again, without
  saying which detail was wrong;
- locked (two failures) → `CallState.locked = True` and `EscalationAgent(reason="verification_failed")`;
- gateway trouble → nothing is verified and the model apologises.

A date or last 4 that is malformed is rejected here and never uses up an attempt.
"""

from __future__ import annotations

import datetime as dt
import re

from livekit.agents import Agent, RunContext, function_tool

from supportagent.agent.base import BaseStateAgent, register_state
from supportagent.agent.context import CallState
from supportagent.agent.states import flow
from supportagent.common.logging import get_logger
from supportagent.contracts.outcome import ContactResult

log = get_logger(__name__)

ASK_FOR_DETAILS = (
    "To protect your account, please tell me your full name, your date of birth "
    "and the last four digits of your loan number."
)
_LAST4 = re.compile(r"^\d{4}$")


def parse_dob(text: str) -> dt.date | None:
    """An ISO date (`1980-01-02`) as the model passes it; anything else is `None`."""
    try:
        return dt.date.fromisoformat(text.strip())
    except ValueError:
        return None


@register_state
class VerifyAgent(BaseStateAgent):
    state_name = flow.VERIFY

    async def on_state_enter(self) -> None:
        self.session.say(ASK_FOR_DETAILS)

    @function_tool
    async def verify_identity(
        self, context: RunContext[CallState], full_name: str, dob: str, loan_last4: str
    ) -> str | Agent | tuple[Agent, str]:
        """Check the caller's identity with the details they gave. Call it once you have all three.

        Args:
            full_name: The caller's full name, exactly as they said it.
            dob: Their date of birth as YYYY-MM-DD.
            loan_last4: The last four digits of their loan number.
        """
        state = context.userdata
        dob_date = parse_dob(dob)
        if dob_date is None:
            return "That date was not clear. Ask the caller to say their full date of birth again."
        if not _LAST4.match(loan_last4.strip()):
            return (
                "That needs to be exactly four digits. Ask the caller for the last four digits of the loan."
            )

        result = await state.gateway.verify_identity(full_name.strip(), dob_date, loan_last4.strip())
        if not result.ok or result.data is None:
            if result.error is not None and result.error.code == "locked":
                return self._locked(state)
            log.warning(
                "verification unavailable", extra={"code": result.error.code if result.error else None}
            )
            return "Verification is not working right now. Apologise and offer to have someone call back."

        data = result.data
        if data.verified:
            state.verified = True
            state.first_name = data.first_name
            state.contact_result = ContactResult.right_party
            return self._on_verified(state)
        if data.locked or data.attempts_remaining == 0:
            return self._locked(state)
        left = data.attempts_remaining
        return (
            f"The details did not match, and the caller has {left} attempt{'s' if left != 1 else ''} left. "
            "Ask them to try again. Do not say which detail was wrong."
        )

    # ------------------------------------------------------------------ code-driven exits

    def _on_verified(self, state: CallState) -> str | Agent:
        # The router sets `after_verification` when an inbound caller asked an account question (used once).
        name = state.after_verification or flow.post_verification_state(
            state.metadata.direction, state.metadata.campaign
        )
        state.after_verification = None
        target = flow.resolve(name)
        if target is None:  # that flow's task has not landed yet: keep the call alive
            log.warning(
                "no state registered after verification", extra={"state": self.state_name, "next": name}
            )
            return (
                "The caller is verified. Thank them and say a colleague will follow up about their account."
            )
        return self.hand_off(self.next_state(target))  # type: ignore[return-value]

    def _locked(self, state: CallState) -> str | Agent:
        state.locked = True
        target = flow.resolve(flow.ESCALATION)
        if target is None:
            log.warning("no escalation state registered", extra={"state": self.state_name})
            return (
                "Verification failed twice. Apologise, say a colleague will call them back, and say goodbye."
            )
        return self.hand_off(self.next_state(target, reason="verification_failed"))  # type: ignore[return-value]
