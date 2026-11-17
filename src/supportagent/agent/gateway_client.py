"""The agent's only door to data: a typed async client for the gateway's MCP tools (AGENT-R1, AGENT-R13).

- `GatewayPort` is the protocol states depend on, so `FakeGateway` (T-EVAL-01) can be injected.
- `GatewayClient` implements it over one MCP session (streamable HTTP) whose requests carry
  `Authorization: Bearer <session token>`. One method per LLM-facing tool plus `record_call_outcome`.
- Every method returns a `ToolResult[Output]`. `ok=false` carries a `ToolError` (`not_verified`,
  `locked`, ...). Transport or protocol failures become `ToolError(code="upstream_error")`, so states
  handle one shape. A failure message never contains the token, arguments or response text.
- `open_session` is the `POST /sessions` call made by worker code (never by the LLM).
"""

from __future__ import annotations

import datetime as dt
import json
from contextlib import AsyncExitStack
from decimal import Decimal
from typing import Protocol, Self

import httpx
import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, SecretStr, ValidationError

from supportagent.common.logging import get_logger
from supportagent.contracts.bank import ProductCode, TimeWindow
from supportagent.contracts.call import CallMetadata
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
    SessionCreated,
    SetContactFlagInput,
    TaskCreatedOutput,
    ToolResult,
    TransactionsOutput,
    VerifyIdentityInput,
    VerifyIdentityOutput,
)
from supportagent.contracts.outcome import CallOutcome

log = get_logger(__name__)

MCP_PATH = "/mcp"
TIMEOUT_S = 10.0


class GatewayUnavailable(RuntimeError):
    """The gateway session could not be opened. The message never contains tokens or bodies."""


class GatewayPort(Protocol):
    """What conversation states may call. `FakeGateway` (T-EVAL-01) and `GatewayClient` implement it."""

    async def lookup_by_phone(self) -> ToolResult[LookupByPhoneOutput]: ...

    async def verify_identity(
        self, full_name: str, dob: dt.date, loan_last4: str
    ) -> ToolResult[VerifyIdentityOutput]: ...

    async def search_policy(
        self, query: str, product: ProductCode | None = None
    ) -> ToolResult[SearchPolicyOutput]: ...

    async def get_loan_status(self, loan_last4: str | None = None) -> ToolResult[LoanStatusOutput]: ...

    async def get_recent_transactions(
        self, loan_last4: str | None = None
    ) -> ToolResult[TransactionsOutput]: ...

    async def get_interaction_history(self) -> ToolResult[InteractionHistoryOutput]: ...

    async def record_promise_to_pay(
        self, promise_date: dt.date, amount: Decimal, loan_last4: str | None = None
    ) -> ToolResult[RecordPromiseOutput]: ...

    async def request_callback(
        self, preferred_at: dt.datetime, reason: str
    ) -> ToolResult[TaskCreatedOutput]: ...

    async def escalate(self, reason: EscalationReason, summary: str) -> ToolResult[EscalateOutput]: ...

    async def set_contact_flag(
        self, flag: ContactFlag, preferred_window: TimeWindow | None = None, note: str = ""
    ) -> ToolResult[ContactFlagOutput]: ...

    async def record_call_outcome(self, outcome: CallOutcome) -> ToolResult[OutcomeRecordedOutput]:
        """Worker only: never exposed to the LLM."""
        ...


def session_base_url(gateway_url: str) -> str:
    """`GATEWAY_URL` points at the MCP endpoint (`…:8002/mcp`); the session API is on its parent."""
    url = gateway_url.rstrip("/")
    return url[: -len(MCP_PATH)] if url.endswith(MCP_PATH) else url


def mcp_url(gateway_url: str) -> str:
    return f"{session_base_url(gateway_url)}{MCP_PATH}"


async def open_session(
    gateway_url: str,
    worker_token: SecretStr,
    meta: CallMetadata,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SecretStr:
    """`POST /sessions` with the worker credential; returns the session token (a secret)."""
    body = SessionCreate(
        call_id=meta.call_id,
        direction=meta.direction,
        campaign=meta.campaign,
        loan_id=meta.loan_id,
        caller_phone=meta.caller_phone,
    )
    headers = {"Authorization": f"Bearer {worker_token.get_secret_value()}"}
    try:
        async with httpx.AsyncClient(
            base_url=session_base_url(gateway_url), headers=headers, timeout=TIMEOUT_S, transport=transport
        ) as http:
            resp = await http.post("/sessions", json=body.model_dump(mode="json", exclude_none=True))
        resp.raise_for_status()
        return SecretStr(SessionCreated.model_validate(resp.json()).session_token)
    except (httpx.HTTPError, ValidationError, ValueError) as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        log.warning("gateway session not opened", extra={"call_id": meta.call_id, "status": status})
        raise GatewayUnavailable(f"gateway session could not be opened (HTTP {status or 'error'})") from None


async def close_session(
    gateway_url: str,
    worker_token: SecretStr,
    call_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """`DELETE /sessions/{call_id}` (idempotent). Best effort: a failure is logged, never raised."""
    headers = {"Authorization": f"Bearer {worker_token.get_secret_value()}"}
    try:
        async with httpx.AsyncClient(
            base_url=session_base_url(gateway_url), headers=headers, timeout=TIMEOUT_S, transport=transport
        ) as http:
            (await http.delete(f"/sessions/{call_id}")).raise_for_status()
    except httpx.HTTPError:
        log.warning("gateway session not closed", extra={"call_id": call_id})


class GatewayClient:
    """Typed MCP client for one call. Use as `async with GatewayClient(...) as gateway:`."""

    def __init__(
        self,
        gateway_url: str,
        session_token: SecretStr,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = mcp_url(gateway_url)
        self._token = session_token
        self._transport = transport
        self._stack: AsyncExitStack | None = None
        self._client: Client | None = None

    def __repr__(self) -> str:  # the token must never reach a log line
        return f"GatewayClient(url={self._url!r}, token=***)"

    async def __aenter__(self) -> Self:
        stack = AsyncExitStack()
        try:
            http = await stack.enter_async_context(
                httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {self._token.get_secret_value()}"},
                    timeout=TIMEOUT_S,
                    transport=self._transport,
                )
            )
            self._client = await stack.enter_async_context(
                Client(streamable_http_client(self._url, http_client=http))
            )
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        return self

    async def __aexit__(self, *exc: object) -> None:
        stack, self._stack, self._client = self._stack, None, None
        if stack is not None:
            await stack.aclose()

    # ------------------------------------------------------------------ transport

    async def _call[T: BaseModel](self, tool: str, output: type[T], args: BaseModel | None) -> ToolResult[T]:
        if self._client is None:
            raise RuntimeError("GatewayClient is not open: use `async with`")
        arguments = args.model_dump(mode="json", exclude_none=True) if args is not None else {}
        try:
            result = await self._client.call_tool(tool, arguments)
            payload = result.structured_content
            if payload is None:
                payload = json.loads(result.content[0].text)  # type: ignore[union-attr]
            return ToolResult[output].model_validate(payload)  # type: ignore[valid-type]
        except Exception as exc:  # transport, protocol or schema failure: one shape for the states
            log.warning("gateway call failed", extra={"tool": tool, "error": type(exc).__name__})
            return ToolResult[output].failure("upstream_error", "gateway unavailable")  # type: ignore[valid-type]

    # ------------------------------------------------------------------ tools

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
        return await self._call(
            "get_recent_transactions", TransactionsOutput, GetTransactionsInput(loan_last4=loan_last4)
        )

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
        """Worker only (finalize). Never wrapped as an LLM tool."""
        return await self._call("record_call_outcome", OutcomeRecordedOutput, outcome)


def _implements_port(client: GatewayClient) -> GatewayPort:
    return client  # static check: GatewayClient satisfies GatewayPort
