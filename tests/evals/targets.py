"""Gateway conformance targets: one `Target` interface over three ways to reach gateway behaviour.

- `fake`: `evals.fakes.FakeGateway` over the persona seed (unit).
- `gateway-fakebank`: the real tool pipeline (`gateway.tools.run_tool`: scope, validation, masking,
  audit) over `tests.gateway.fakes.FakeBank`, the same persona seed (unit).
- `gateway-db`: the real pipeline over the real bank API and the seeded `bank_test` database (integration).

`InProcessPort` implements `GatewayPort` by calling `run_tool` with exactly the arguments
`GatewayClient` would send over MCP (`model_dump(mode="json", exclude_none=True)`), so the only thing the
real targets skip is the HTTP hop.
"""

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from supportagent.contracts.bank import ProductCode, TimeWindow
from supportagent.contracts.gateway import (
    ContactFlag,
    ContactFlagOutput,
    EscalateInput,
    EscalateOutput,
    EscalationReason,
    GetLoanStatusInput,
    GetTransactionsInput,
    InteractionHistoryInput,
    InteractionHistoryOutput,
    LoanStatusOutput,
    LookupByPhoneInput,
    LookupByPhoneOutput,
    OutcomeRecordedOutput,
    RecordPromiseInput,
    RecordPromiseOutput,
    RequestCallbackInput,
    SearchPolicyInput,
    SearchPolicyOutput,
    SessionCreate,
    SetContactFlagInput,
    TaskCreatedOutput,
    ToolResult,
    TransactionsOutput,
    VerifyIdentityInput,
    VerifyIdentityOutput,
)
from supportagent.contracts.outcome import CallOutcome, Campaign, Direction
from supportagent.evals.fakes import FakeBankData, FakeGateway
from supportagent.gateway.config import GatewayConfig
from supportagent.gateway.tools import Gateway, run_tool
from tests.gateway.fakes import FakeBank, _status

SALT = "conformance-salt"
AuditRows = list[tuple[str, str, int | None, str]]  # (tool, decision, customer_id, args_hash)


@dataclass
class Target:
    """One call (session) on one implementation: the port the agent uses, and that call's audit rows."""

    port: Any  # a GatewayPort
    call_id: str
    audit: Callable[[], Awaitable[AuditRows]]


class InProcessPort:
    """A `GatewayPort` over the real tool pipeline of one `Gateway`, for one session token."""

    def __init__(self, gateway: Gateway, token: str) -> None:
        self.gateway, self.token = gateway, token

    async def _call[T: BaseModel](self, tool: str, output: type[T], args: BaseModel) -> ToolResult[T]:
        raw = args.model_dump(mode="json", exclude_none=True)
        result = await run_tool(self.gateway, tool, self.token, raw)
        return ToolResult[output].model_validate(result.model_dump(mode="json"))  # type: ignore[valid-type]

    async def lookup_by_phone(self) -> ToolResult[LookupByPhoneOutput]:
        return await self._call("lookup_by_phone", LookupByPhoneOutput, LookupByPhoneInput())

    async def verify_identity(
        self, full_name: str, dob: dt.date, loan_last4: str
    ) -> ToolResult[VerifyIdentityOutput]:
        args = VerifyIdentityInput(full_name=full_name, dob=dob, loan_last4=loan_last4)
        return await self._call("verify_identity", VerifyIdentityOutput, args)

    async def search_policy(
        self, query: str, product: ProductCode | None = None
    ) -> ToolResult[SearchPolicyOutput]:
        return await self._call(
            "search_policy", SearchPolicyOutput, SearchPolicyInput(query=query, product=product)
        )

    async def get_loan_status(self, loan_last4: str | None = None) -> ToolResult[LoanStatusOutput]:
        return await self._call(
            "get_loan_status", LoanStatusOutput, GetLoanStatusInput(loan_last4=loan_last4)
        )

    async def get_recent_transactions(self, loan_last4: str | None = None) -> ToolResult[TransactionsOutput]:
        args = GetTransactionsInput(loan_last4=loan_last4)
        return await self._call("get_recent_transactions", TransactionsOutput, args)

    async def get_interaction_history(self) -> ToolResult[InteractionHistoryOutput]:
        return await self._call(
            "get_interaction_history", InteractionHistoryOutput, InteractionHistoryInput()
        )

    async def record_promise_to_pay(
        self, promise_date: dt.date, amount: Decimal, loan_last4: str | None = None
    ) -> ToolResult[RecordPromiseOutput]:
        args = RecordPromiseInput(promise_date=promise_date, amount=amount, loan_last4=loan_last4)
        return await self._call("record_promise_to_pay", RecordPromiseOutput, args)

    async def request_callback(self, preferred_at: dt.datetime, reason: str) -> ToolResult[TaskCreatedOutput]:
        args = RequestCallbackInput(preferred_at=preferred_at, reason=reason)
        return await self._call("request_callback", TaskCreatedOutput, args)

    async def escalate(self, reason: EscalationReason, summary: str) -> ToolResult[EscalateOutput]:
        return await self._call("escalate", EscalateOutput, EscalateInput(reason=reason, summary=summary))

    async def set_contact_flag(
        self, flag: ContactFlag, preferred_window: TimeWindow | None = None, note: str = ""
    ) -> ToolResult[ContactFlagOutput]:
        args = SetContactFlagInput(flag=flag, preferred_window=preferred_window, note=note)
        return await self._call("set_contact_flag", ContactFlagOutput, args)

    async def record_call_outcome(self, outcome: CallOutcome) -> ToolResult[OutcomeRecordedOutput]:
        return await self._call("record_call_outcome", OutcomeRecordedOutput, outcome)


def fake_bank_data(bank: FakeBank) -> FakeBankData:
    """The persona seed in `FakeBank` as `FakeBankData` (so the fake gateway sees what the real one sees)."""
    return FakeBankData(
        as_of=bank.as_of,
        customers=dict(bank.customers),
        loans=dict(bank.loans),
        statuses={ln_id: s for ln_id in bank.loans if (s := _status(bank, ln_id)) is not None},
        transactions=list(bank.txs),
        interactions=list(bank.interactions.values()),
        promises=list(bank.promises),
        prefs=dict(bank.prefs),
    )


def session_request(**overrides: Any) -> SessionCreate:
    values: dict[str, Any] = {
        "call_id": str(uuid.uuid4()),
        "direction": Direction.outbound,
        "campaign": Campaign.missed_payment,
    }
    values.update(overrides)
    return SessionCreate(**values)


def gateway_config(service_token: str = "service-tok") -> GatewayConfig:
    return GatewayConfig(worker_token="worker-tok", service_token=service_token, audit_salt=SALT)


async def open_fake(req: SessionCreate) -> Target:
    gw = FakeGateway(
        fake_bank_data(FakeBank()),
        direction=req.direction,
        campaign=req.campaign,
        loan_id=req.loan_id,
        caller_phone=req.caller_phone,
        call_id=req.call_id,
        salt=SALT,
    )

    async def audit() -> AuditRows:
        return [(a.tool, a.decision, a.customer_id, a.args_hash) for a in gw.audit]

    return Target(gw, req.call_id, audit)


async def open_gateway_fakebank(req: SessionCreate) -> Target:
    bank = FakeBank()
    gw = Gateway(bank=bank, config=gateway_config())  # type: ignore[arg-type]
    session = await gw.store.create(req, gw.bank)

    async def audit() -> AuditRows:
        return [
            (a.tool, a.decision, a.customer_id, a.args_hash) for a in bank.audit if a.call_id == req.call_id
        ]

    return Target(InProcessPort(gw, session.token), req.call_id, audit)
