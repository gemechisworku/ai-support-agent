"""`CallbackAgent`: the callback call with context (U5, AGENT-R10). Outbound, `campaign=callback`.

The caller asked to be called back, or promised a payment that did not arrive. By the time the call is here
the caller is verified (an unverified one is sent to verification first, with nothing read and nothing said
about money). In code, in this order:

1. `get_interaction_history` and `get_loan_status` are read. A failed read says a fixed line, no figure, and
   goes to the closing state.
2. **Nothing outstanding** (no loan with an overdue amount): a fixed, polite line with no figure
   (`NOTHING_OPEN_LINE`) and the closing state. A broken promise that has since been paid up is not chased.
3. **Something outstanding**: the call is about an overdue amount, so the collections disclosure is spoken
   first, verbatim and uninterruptible, as in the collections flow. Then the **context line**, in code:
   - a broken promise from the history: "You'd planned to pay $X on D, and we haven't seen that payment yet"
     (`context_line`, amount and date from the history only);
   - with no broken promise, the overdue amount and date from the loan status (`purpose_line`).
   The code then asks for a new date and names the amount, which is the loan status's overdue amount. The
   model never says an amount or a date.
4. **The gateway decides the new promise** (`record_promise_to_pay`): an accepted promise goes to the closing
   read-back; a rejection gives the gateway's own `max_date` or `amount_due`; after `MAX_REJECTIONS` a
   callback is offered. A callback is promised only once the gateway created it (`offer_callback`).

Hardship and dispute follow the collections states' path (`refer_to_human`, the interrupt detector in code,
and the honest "cannot transfer, callback booked" line while escalation is T-AGENT-11). Interrupts (AGENT-R3)
and a caller farewell are checked in code before the model replies.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from livekit.agents import Agent, RunContext, function_tool

from supportagent.agent.base import register_state
from supportagent.agent.context import CallState
from supportagent.agent.disclose import speak_disclosure
from supportagent.agent.states import flow
from supportagent.agent.states.account import last4
from supportagent.agent.states.collections import (
    ACCEPTED_NOTE,
    BAD_DATE,
    DISCLOSURE_KEY,
    MAX_REJECTIONS,
    PROMISE_FAILED,
    REJECTED_AMOUNT,
    REJECTED_DATE,
    TOO_MANY_REJECTIONS,
    ReferralKind,
    _CollectionsState,
    overdue_loan,
    purpose_line,
)
from supportagent.agent.summary import format_callback, format_date, format_money
from supportagent.common import clock
from supportagent.common.logging import get_logger
from supportagent.contracts.gateway import LoanSummary, PromiseOut

log = get_logger(__name__)

FOLLOW_UP_DELAY = dt.timedelta(days=1)  # a colleague calls back within a day
MAX_REASON_CHARS = 200
CALLBACK_REASON = "callback call: caller could not agree a payment date"

UNAVAILABLE_LINE = "I'm sorry, I can't pull up your account details right now."
NOTHING_OPEN_LINE = "I don't see anything outstanding on your account right now."
NOT_VERIFIED_LINE = "Before I go on, I need to confirm who I'm speaking with."
CALLBACK_FAILED = (
    "The callback could not be booked. Apologise briefly and say you could not arrange it on this call. "
    "Do not say that anyone will contact the caller."
)


def broken_promise(promises: list[PromiseOut]) -> PromiseOut | None:
    """The latest promise the history marks `broken` (None when there is none). Open ones do not count."""
    broken = [p for p in promises if p.status == "broken"]
    return max(broken, key=lambda p: p.promised_date) if broken else None


def context_line(promise: PromiseOut) -> str:
    """What the agent says first for a broken promise, rendered from the history (never by the model)."""
    return (
        f"You'd planned to pay {format_money(promise.amount)} on {format_date(promise.promised_date)}, "
        "and we haven't seen that payment yet."
    )


def ask_line(loan: LoanSummary) -> str:
    """The question for the new date, naming the amount from the loan status."""
    return f"On what date can you make a payment of {format_money(loan.overdue_amount)}?"


def _today_text() -> str:
    today = clock.now().astimezone(dt.UTC).date()
    return f"{today:%A} {format_date(today)} (ISO {today.isoformat()})"


@register_state
class CallbackAgent(_CollectionsState):
    state_name = flow.CALLBACK

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.loan_last4: str | None = None
        self.amount: Decimal | None = None  # the amount to promise, fixed by the code from `get_loan_status`
        self._rejections = 0

    async def on_state_enter(self) -> None:
        state = self.state
        if not state.verified:  # never read data or speak a figure to an unverified caller
            await self._move_on(NOT_VERIFIED_LINE, flow.VERIFY)
            return
        history = await state.gateway.get_interaction_history()
        status = await state.gateway.get_loan_status()
        if not history.ok or history.data is None or not status.ok or status.data is None:
            failed = history if not history.ok or history.data is None else status
            code = failed.error.code if failed.error else "unknown"
            log.warning("callback: context not read", extra={"error_code": code})
            await self._move_on(UNAVAILABLE_LINE, flow.CLOSING)
            return
        loan = overdue_loan(status.data.loans)
        if loan is None:  # nothing outstanding (this also covers a broken promise paid up since)
            await self._move_on(NOTHING_OPEN_LINE, flow.CLOSING)
            return
        self.loan_last4 = last4(loan)
        self.amount = loan.overdue_amount
        speak_disclosure(self, DISCLOSURE_KEY)  # first, verbatim, before any figure
        promise = broken_promise(history.data.open_promises)
        self.session.say(context_line(promise) if promise is not None else purpose_line(loan))
        self.session.say(ask_line(loan))

    # ------------------------------------------------------------------ tools

    @function_tool
    async def record_promise_to_pay(
        self, context: RunContext[CallState], promise_date: str
    ) -> str | Agent | tuple[Agent, str]:
        """Record the date the caller promises to pay. The system decides whether the date is allowed.

        Args:
            promise_date: The date as ISO 8601 (YYYY-MM-DD). Today's date is in the instructions.
        """
        state = context.userdata
        try:
            day = dt.date.fromisoformat(promise_date.strip())
        except ValueError:
            return BAD_DATE
        if self.amount is None:  # not expected: the state sets it before the caller can answer
            return PROMISE_FAILED
        result = await state.gateway.record_promise_to_pay(day, self.amount, self.loan_last4)
        if not result.ok or result.data is None:
            code = result.error.code if result.error else "unknown"
            log.warning("callback: promise not recorded", extra={"error_code": code})
            return PROMISE_FAILED
        out = result.data
        if out.accepted:
            state.outcome.record_promise(day, self.amount)  # the arguments the gateway accepted
            closing = flow.resolve(flow.CLOSING)
            if closing is None:  # always registered; keep the call alive regardless
                return ACCEPTED_NOTE
            return self.hand_off(self.next_state(closing), ACCEPTED_NOTE)
        if out.reason == "loan_not_found":
            return PROMISE_FAILED
        self._rejections += 1
        if out.reason == "amount_invalid":
            self.amount = out.amount_due  # the gateway's amount is the one that can be agreed
        if self._rejections >= MAX_REJECTIONS:
            return TOO_MANY_REJECTIONS
        template = REJECTED_AMOUNT if out.reason == "amount_invalid" else REJECTED_DATE
        return template.format(
            amount=format_money(out.amount_due),
            max_date=format_date(out.max_date),
            iso=out.max_date.isoformat(),
            today=_today_text(),
        )

    @function_tool
    async def offer_callback(self, context: RunContext[CallState]) -> str:
        """Book a call back from a colleague, only after the caller has said yes to one."""
        state = context.userdata
        when = clock.now() + FOLLOW_UP_DELAY
        result = await state.gateway.request_callback(when, CALLBACK_REASON[:MAX_REASON_CHARS])
        if not result.ok or result.data is None:
            code = result.error.code if result.error else "unknown"
            log.warning("callback state: callback not booked", extra={"error_code": code})
            return CALLBACK_FAILED
        state.outcome.record_callback(result.data.due_at)  # the time the gateway scheduled, not the request
        when_text = format_callback(result.data.due_at)
        return f"Booked. Tell the caller a colleague will call them back on {when_text}."

    @function_tool
    async def refer_to_human(
        self, context: RunContext[CallState], reason: ReferralKind
    ) -> str | Agent | tuple[Agent, str]:
        """Pass the caller to a person: hardship (cannot pay: lost income, a hard situation) or a dispute.

        Args:
            reason: hardship or dispute.
        """
        return await self._refer(reason)

    @function_tool
    async def finish(self, context: RunContext[CallState]) -> str | Agent:
        """The caller has nothing more to say about this call."""
        closing = flow.resolve(flow.CLOSING)
        if closing is None:  # always registered; keep the call alive regardless
            return "Ask whether there is anything else you can help with."
        return self.hand_off(self.next_state(closing))  # type: ignore[return-value]
