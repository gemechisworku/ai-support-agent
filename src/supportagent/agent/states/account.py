"""`AccountAgent`: balance, next due date and amount, last payment, payoff estimate, status (U3, AGENT-R5).

The router hands over a verified caller whose question is already in the chat history, so the entry action
just answers it (`session.generate_reply()`). The model never sees raw data and never computes a figure:

- `get_loan_status` and `get_recent_transactions` read through the gateway and return *text built here in
  code* from the contract's fields, using the gateway's own `speakable` strings wherever there is one. The
  balance and the payoff estimate have no `speakable`, so the contract's `Decimal` is rendered as `$1,234.56`
  and nothing more (no rounding, no sums).
- With more than one loan the tool does not answer. It returns the loan list and an instruction to ask which
  one, so no amount is said before the caller chooses; the next call is scoped by `loan_last4`. The loan list
  is fetched once to find out: `get_recent_transactions` does not say which loan a payment belongs to.
- The payoff figure is the outstanding balance, labelled "estimate", with the policy caveat attached.
- An unverified caller gets nothing: `CallState`'s `VerifiedGateway` answers `not_verified` before the gateway
  is reached (this state is only entered verified; the guard is the backstop).
- A failed read says only that the account system did not answer; no error detail reaches the model.

Every figure the agent says should appear in a tool result of the same call: `supportagent.agent.grounding`
checks that in tests (and EVAL-03 over whole calls). When the caller is done, `finish` hands back to the
router, so one call can move between account and policy questions.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from livekit.agents import Agent, RunContext, function_tool

from supportagent.agent.base import BaseStateAgent, register_state
from supportagent.agent.context import CallState
from supportagent.agent.states import flow
from supportagent.agent.states.router import RouterAgent
from supportagent.common.logging import get_logger
from supportagent.contracts.gateway import LoanSummary, ToolResult

log = get_logger(__name__)

CLARIFY_LOAN = (
    "The caller has more than one loan. Ask which one they mean, by product or by the last four digits, "
    "then call this tool again with that loan_last4. Say no amount or date yet."
)
NEED_LAST4 = "Ask the caller for all four digits of the loan number they mean, then call this tool again."
NOT_VERIFIED_NOTE = (
    "Account details cannot be shared until the caller's identity is verified. Say that, and give no figure."
)
UNAVAILABLE_NOTE = (
    "The account system did not answer. Tell the caller you cannot get that right now and offer a follow-up. "
    "Do not guess or estimate any figure."
)
PAYOFF_CAVEAT = (
    "This is an estimate, not a quote: the exact amount on a given day also includes interest up to that "
    "day, any unpaid fees and any prepayment fee, and the formal payoff quote is issued in writing."
)
PENDING_NOTE = "A payment marked pending has not posted yet: say so, and do not call it paid."
NO_PAYMENTS = "No recent payments on this loan. Say so plainly; give no figure."
NO_LOAN = "No loan was found for this caller. Say so, and offer a follow-up."
READ_AS_WRITTEN = "Say amounts and dates exactly as written here; never round, add up or work anything out."

_LAST4 = re.compile(r"\d{4}")


def money(value: Decimal) -> str:
    """A contract `Decimal` rendered as `$1,234.56`. Rendering only: the gateway already rounded it."""
    return f"${value:,.2f}"


def last4(loan: LoanSummary) -> str:
    return loan.loan_masked[-4:]


def loan_label(loan: LoanSummary) -> str:
    return f"{loan.product_name} ending {last4(loan)}"


def choices(loans: list[LoanSummary]) -> str:
    return "Loans: " + "; ".join(loan_label(ln) for ln in loans) + "."


def loan_facts(loan: LoanSummary) -> str:
    """Everything the caller may ask about one loan, from the tool result's fields and `speakable`."""
    lines = [
        f"{loan_label(loan)}, status {loan.status}.",
        f"Outstanding balance: {money(loan.outstanding_balance)}.",
        f"Payments: {loan.speakable}.",
    ]
    if loan.days_past_due:
        unit = "day" if loan.days_past_due == 1 else "days"
        lines.append(f"{loan.days_past_due} {unit} past due.")
    lines.append(f"Payoff estimate: about {money(loan.outstanding_balance)}. {PAYOFF_CAVEAT}")
    lines.append(READ_AS_WRITTEN)
    return "\n".join(lines)


@register_state
class AccountAgent(BaseStateAgent):
    state_name = flow.ACCOUNT

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._loans: list[LoanSummary] | None = (
            None  # the caller's loans, once read (to spot "more than one")
        )

    async def on_state_enter(self) -> None:
        # The router handed over silently: the caller's question is the last message in the chat history.
        self.session.generate_reply()

    # ------------------------------------------------------------------ tools

    @function_tool
    async def get_loan_status(self, context: RunContext[CallState], loan_last4: str | None = None) -> str:
        """Read the caller's loan: balance, what is overdue, next payment, status and the payoff estimate.

        Args:
            loan_last4: The last four digits of the loan, only after the caller has said which one when they
                have more than one. Leave it out the first time.
        """
        scoped = loan_last4 is not None
        if scoped and not _LAST4.fullmatch(loan_last4.strip()):  # type: ignore[union-attr]
            return NEED_LAST4
        wanted = loan_last4.strip() if scoped and loan_last4 else None
        result = await context.userdata.gateway.get_loan_status(wanted)
        failure = _failure("get_loan_status", result)
        if failure or result.data is None:
            return failure or UNAVAILABLE_NOTE
        loans = result.data.loans
        if scoped and not loans:  # those digits match none of their loans: show the real choices
            return await self._ask_which(context, "No loan matches those digits. ")
        if not loans:
            return NO_LOAN
        if not scoped:
            self._loans = loans
            if len(loans) > 1:
                return f"{choices(loans)}\n{CLARIFY_LOAN}"
        return loan_facts(loans[0])

    @function_tool
    async def get_recent_transactions(
        self, context: RunContext[CallState], loan_last4: str | None = None
    ) -> str:
        """Read the caller's latest payments on a loan, newest first, including pending ones.

        Args:
            loan_last4: The last four digits of the loan, only after the caller has said which one when they
                have more than one. Leave it out the first time.
        """
        scoped = loan_last4 is not None
        if scoped and not _LAST4.fullmatch(loan_last4.strip()):  # type: ignore[union-attr]
            return NEED_LAST4
        wanted = loan_last4.strip() if scoped and loan_last4 else None
        if not scoped:
            # Payments do not say which loan they belong to, so find out whether there is more than one.
            if self._loans is None:
                status = await context.userdata.gateway.get_loan_status(None)
                failure = _failure("get_loan_status", status)
                if failure or status.data is None:
                    return failure or UNAVAILABLE_NOTE
                self._loans = status.data.loans
            if len(self._loans) > 1:
                return f"{choices(self._loans)}\n{CLARIFY_LOAN}"
        result = await context.userdata.gateway.get_recent_transactions(wanted)
        failure = _failure("get_recent_transactions", result)
        if failure or result.data is None:
            return failure or UNAVAILABLE_NOTE
        rows = result.data.transactions
        if not rows:
            return NO_PAYMENTS
        lines = ["Recent payments, newest first:"] + [f"- {row.speakable}" for row in rows]
        if any(row.status == "pending" for row in rows):
            lines.append(PENDING_NOTE)
        lines.append(READ_AS_WRITTEN)
        return "\n".join(lines)

    @function_tool
    async def finish(self, context: RunContext[CallState]) -> str | Agent:
        """The caller has what they need, or asks something that is not about their own account."""
        return self.hand_off(self.next_state(RouterAgent))  # type: ignore[return-value]

    # ------------------------------------------------------------------ helpers

    async def _ask_which(self, context: RunContext[CallState], lead: str) -> str:
        """The caller named a loan that is not theirs: list the real ones and ask again."""
        result = await context.userdata.gateway.get_loan_status(None)
        failure = _failure("get_loan_status", result)
        if failure or result.data is None:
            return failure or UNAVAILABLE_NOTE
        self._loans = result.data.loans
        if not self._loans:
            return NO_LOAN
        return f"{lead}{choices(self._loans)}\n{CLARIFY_LOAN}"


def _failure(tool: str, result: ToolResult[Any]) -> str | None:
    """The note the model gets when a read failed (`None` when it succeeded). Only the code is logged."""
    if result.ok:
        return None
    code = result.error.code if result.error else "unknown"
    log.warning("account read failed", extra={"tool": tool, "error_code": code})
    return NOT_VERIFIED_NOTE if code == "not_verified" else UNAVAILABLE_NOTE
