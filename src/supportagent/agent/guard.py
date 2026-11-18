"""Verification-before-data, in code (AGENT-R2, constitution II/III).

`VerifiedGateway` wraps a `GatewayPort` and refuses the tools that return or change account data until the
caller is verified: it answers `not_verified` itself, and the gateway is never reached. The gateway
enforces the same rule server side, so this is defence in depth that also keeps a blocked call out of the
audit trail's "allowed" rows. `CallState` installs it around its gateway, driven by `CallState.verified`.

Not guarded, because they are allowed (or needed) before verification: `lookup_by_phone` and
`verify_identity` (they establish identity), `search_policy` (policy questions need no identity),
`request_callback`, `escalate` and `set_contact_flag` (the gateway decides, per its own rules), and
`record_call_outcome` (the worker always writes one).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal

from supportagent.agent.gateway_client import GatewayPort
from supportagent.contracts.bank import ProductCode, TimeWindow
from supportagent.contracts.gateway import (
    ContactFlag,
    ContactFlagOutput,
    EscalateOutput,
    EscalationReason,
    InteractionHistoryOutput,
    LoanStatusOutput,
    LookupByPhoneOutput,
    OutcomeRecordedOutput,
    RecordPromiseOutput,
    SearchPolicyOutput,
    TaskCreatedOutput,
    ToolResult,
    TransactionsOutput,
    VerifyIdentityOutput,
)
from supportagent.contracts.outcome import CallOutcome

# Account-data reads (the same set the compliance check looks for) plus the one write that carries an amount.
GUARDED_TOOLS = frozenset(
    {"get_loan_status", "get_recent_transactions", "get_interaction_history", "record_promise_to_pay"}
)
NOT_VERIFIED_MESSAGE = "identity is not verified yet"


class VerifiedGateway:
    """A `GatewayPort` that refuses account-data tools until `is_verified()` is true."""

    def __init__(self, inner: GatewayPort, is_verified: Callable[[], bool]) -> None:
        self.inner = inner
        self._is_verified = is_verified

    def _blocked(self) -> bool:
        return not self._is_verified()

    # ------------------------------------------------------------------ never guarded

    async def lookup_by_phone(self) -> ToolResult[LookupByPhoneOutput]:
        return await self.inner.lookup_by_phone()

    async def verify_identity(
        self, full_name: str, dob: dt.date, loan_last4: str
    ) -> ToolResult[VerifyIdentityOutput]:
        return await self.inner.verify_identity(full_name, dob, loan_last4)

    async def search_policy(
        self, query: str, product: ProductCode | None = None
    ) -> ToolResult[SearchPolicyOutput]:
        return await self.inner.search_policy(query, product)

    async def request_callback(self, preferred_at: dt.datetime, reason: str) -> ToolResult[TaskCreatedOutput]:
        return await self.inner.request_callback(preferred_at, reason)

    async def escalate(self, reason: EscalationReason, summary: str) -> ToolResult[EscalateOutput]:
        return await self.inner.escalate(reason, summary)

    async def set_contact_flag(
        self, flag: ContactFlag, preferred_window: TimeWindow | None = None, note: str = ""
    ) -> ToolResult[ContactFlagOutput]:
        return await self.inner.set_contact_flag(flag, preferred_window, note)

    async def record_call_outcome(self, outcome: CallOutcome) -> ToolResult[OutcomeRecordedOutput]:
        return await self.inner.record_call_outcome(outcome)

    # ------------------------------------------------------------------ guarded

    async def get_loan_status(self, loan_last4: str | None = None) -> ToolResult[LoanStatusOutput]:
        if self._blocked():
            return ToolResult.failure("not_verified", NOT_VERIFIED_MESSAGE)
        return await self.inner.get_loan_status(loan_last4)

    async def get_recent_transactions(self, loan_last4: str | None = None) -> ToolResult[TransactionsOutput]:
        if self._blocked():
            return ToolResult.failure("not_verified", NOT_VERIFIED_MESSAGE)
        return await self.inner.get_recent_transactions(loan_last4)

    async def get_interaction_history(self) -> ToolResult[InteractionHistoryOutput]:
        if self._blocked():
            return ToolResult.failure("not_verified", NOT_VERIFIED_MESSAGE)
        return await self.inner.get_interaction_history()

    async def record_promise_to_pay(
        self, promise_date: dt.date, amount: Decimal, loan_last4: str | None = None
    ) -> ToolResult[RecordPromiseOutput]:
        if self._blocked():
            return ToolResult.failure("not_verified", NOT_VERIFIED_MESSAGE)
        return await self.inner.record_promise_to_pay(promise_date, amount, loan_last4)


def _implements_port(guard: VerifiedGateway) -> GatewayPort:
    return guard  # static check: the guard satisfies GatewayPort
