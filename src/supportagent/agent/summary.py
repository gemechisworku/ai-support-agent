"""The post-call summary and the closing read-back: deterministic templates over outcome fields (AGENT-R12).

No model writes these. `compose_summary` takes only fields that the call established through tool results
(the accepted promise, the callback the gateway scheduled, the escalation target, the contact flags), so it
cannot introduce a figure the tools never returned. `ungrounded_figures` is the guard: every number in a
summary must come from the formatted promise or callback, and an unverified call's summary has no figures
at all. The bank places the finished summary in the outbox when it stores the outcome.

Dates read "4 November 2026"; times are in UTC ("at 14:30 UTC") because the callback is stored as an aware
datetime. Amounts read "$1,234.50".
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from decimal import Decimal

from supportagent.contracts.outcome import Callback, ContactResult, Escalation, Intent, PromiseToPay

CALLBACK_QUEUE = "callback_queue"  # `Escalation.target` when no human was available and a ticket was made

_FIGURE = re.compile(r"\d+(?:[.,:]\d+)*")


def format_money(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def format_date(day: dt.date) -> str:
    return f"{day.day} {day:%B %Y}"


def format_callback(when: dt.datetime) -> str:
    utc = when.astimezone(dt.UTC)
    return f"{format_date(utc.date())} at {utc:%H:%M} UTC"


def figures_in(text: str) -> set[str]:
    """The numbers in `text` ("200.72", "12", "2026", "14:30"), for the grounding check."""
    return set(_FIGURE.findall(text))


def ungrounded_figures(text: str, *, promise: PromiseToPay | None, callback: Callback | None) -> set[str]:
    """Figures in `text` that do not come from the promise or the callback (empty means grounded)."""
    allowed: set[str] = set()
    if promise is not None:
        allowed |= figures_in(f"{format_money(promise.amount)} {format_date(promise.date)}")
    if callback is not None:
        allowed |= figures_in(format_callback(callback.datetime))
    return figures_in(text) - allowed


def readback_line(*, promise: PromiseToPay | None, callback: Callback | None) -> str | None:
    """What the closing state repeats to the caller: only commitments the call recorded, else `None`."""
    parts: list[str] = []
    if promise is not None:
        parts.append(f"you will pay {format_money(promise.amount)} by {format_date(promise.date)}")
    if callback is not None:
        parts.append(f"we will call you back on {format_callback(callback.datetime)}")
    if not parts:
        return None
    return "To confirm: " + ", and ".join(parts) + "."


_UNVERIFIED: dict[ContactResult, str] = {
    ContactResult.third_party: (
        "We reached someone other than the account holder and left a callback request. "
        "No account details were shared."
    ),
    ContactResult.wrong_number: "We were told this is a wrong number. No account details were shared.",
    ContactResult.no_answer: "We could not reach the customer.",
    ContactResult.voicemail: "We could not reach the customer.",
    ContactResult.busy: "We could not reach the customer.",
    ContactResult.right_party: (
        "We could not verify the caller's identity, so no account details were shared."
    ),
}


def compose_summary(
    *,
    bank_name: str,
    verified: bool,
    contact_result: ContactResult,
    intents: Sequence[Intent],
    promise: PromiseToPay | None,
    callback: Callback | None,
    escalated: Escalation | None,
    dispute_flag: bool,
    cease_contact_flag: bool,
) -> str:
    """The summary for the outbox. Unverified calls get a neutral line with no figures."""
    if not verified:
        return _UNVERIFIED[contact_result]
    parts = [f"Thank you for speaking with {bank_name}."]
    if cease_contact_flag:
        parts.append("As you asked, we have recorded your request to stop phone calls.")
    if dispute_flag:
        parts.append("We have noted your dispute and a colleague will review it.")
    if escalated is not None:
        if escalated.target == CALLBACK_QUEUE:
            parts.append("A colleague will call you back.")
        else:
            parts.append("We connected you with a colleague.")
    if promise is not None:
        parts.append(f"You promised to pay {format_money(promise.amount)} by {format_date(promise.date)}.")
    if callback is not None:
        parts.append(f"We will call you back on {format_callback(callback.datetime)}.")
    if Intent.intent_to_pay in intents:
        parts.append("You told us you intend to pay.")
    if Intent.already_paid in intents:
        parts.append("You told us you have already paid, and we will check our records.")
    if Intent.account_question in intents:
        parts.append("We answered your question about your account.")
    if Intent.policy_question in intents:
        parts.append("We answered your question about our policies.")
    return " ".join(parts)
