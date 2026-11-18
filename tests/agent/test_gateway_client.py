"""T-AGENT-01 · AGENT-R1/R13: `GatewayClient` against an in-memory fake MCP server.

The fake is a real MCP `Server` served through an ASGI transport (no sockets), so the bearer header travels
the same HTTP path as in production. It records the header and the arguments of every call.
"""

import datetime as dt
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
import httpx2
import mcp_types as types
import pytest
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from pydantic import SecretStr

from supportagent.agent.gateway_client import (
    GatewayClient,
    GatewayPort,
    GatewayUnavailable,
    close_session,
    mcp_url,
    open_session,
    session_base_url,
)
from supportagent.contracts.call import CallMetadata
from supportagent.contracts.gateway import TOOL_MODELS, EscalationReason, ToolError
from supportagent.contracts.outcome import CallOutcome, Campaign, ContactResult, Direction, Versions

CALL_ID = "fbde0dde-804f-4a79-ba80-2795d29b3079"
TOKEN = "session-token-SECRET-123"
GATEWAY_URL = "http://gateway.test:8002/mcp"

LOAN = {
    "loan_masked": "****4417",
    "product_code": "PERSONAL",
    "product_name": "Personal Loan",
    "outstanding_balance": "4100.00",
    "overdue_amount": "200.72",
    "oldest_unpaid_due_date": "2026-11-04",
    "next_due_date": "2026-12-04",
    "next_due_amount": "245.10",
    "days_past_due": 5,
    "fees_accrued": "0.00",
    "status": "active",
    "speakable": "$200.72 overdue since 4 November 2026",
}


def ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def fail(code: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": {"code": code, "message": ""}}


@dataclass
class FakeMcp:
    """Replies per tool name; records the bearer header and arguments of each call."""

    replies: dict[str, dict[str, Any]] = field(default_factory=dict)
    headers: list[str | None] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    broken: bool = False

    def server(self) -> Server[Any]:
        async def on_list_tools(
            ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams | None
        ) -> types.ListToolsResult:
            names = [*TOOL_MODELS, "record_call_outcome"]
            return types.ListToolsResult(
                tools=[types.Tool(name=n, description=n, input_schema={"type": "object"}) for n in names]
            )

        async def on_call_tool(
            ctx: ServerRequestContext[Any, Any], params: types.CallToolRequestParams
        ) -> types.CallToolResult:
            self.headers.append(getattr(ctx.request, "headers", {}).get("authorization"))
            self.calls.append((params.name, dict(params.arguments or {})))
            if self.broken:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="not json")], is_error=True
                )
            payload = self.replies.get(params.name, fail("not_found"))
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(payload))],
                structured_content=payload,
                is_error=False,
            )

        return Server("fake-gateway", version="1.0", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


@asynccontextmanager
async def open_fake() -> AsyncIterator[tuple[FakeMcp, GatewayClient]]:
    """The fake server plus a `GatewayClient` connected to it (opened and closed in the same task)."""
    state = FakeMcp()
    server = state.server()
    app = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0"
    )
    async with server.session_manager.run():
        transport = httpx2.ASGITransport(app=app)
        async with GatewayClient(GATEWAY_URL, SecretStr(TOKEN), transport=transport) as client:
            yield state, client


async def test_the_session_token_is_sent_as_a_bearer_header() -> None:
    async with open_fake() as (state, client):
        state.replies["lookup_by_phone"] = ok({"match": True, "first_name": "Frank"})
        result = await client.lookup_by_phone()
        assert state.headers == [f"Bearer {TOKEN}"]
        assert result.ok and result.data is not None and result.data.first_name == "Frank"


async def test_envelope_is_parsed_into_typed_models() -> None:
    async with open_fake() as (state, client):
        state.replies["get_loan_status"] = ok({"loans": [LOAN]})
        result = await client.get_loan_status()
        assert result.ok and result.error is None and result.data is not None
        loan = result.data.loans[0]
        assert loan.days_past_due == 5
        assert loan.overdue_amount == Decimal("200.72")  # Decimal, never float
        assert loan.oldest_unpaid_due_date == dt.date(2026, 11, 4)
        assert state.calls == [("get_loan_status", {})]


async def test_ok_false_maps_to_a_tool_error() -> None:
    async with open_fake() as (state, client):
        state.replies["get_loan_status"] = fail("not_verified")
        result = await client.get_loan_status()
        assert not result.ok and result.data is None
        assert isinstance(result.error, ToolError) and result.error.code == "not_verified"


async def test_arguments_are_typed_and_carry_no_customer_id() -> None:
    async with open_fake() as (state, client):
        state.replies["verify_identity"] = ok(
            {"verified": True, "attempts_remaining": 2, "locked": False, "first_name": "Frank"}
        )
        state.replies["record_promise_to_pay"] = ok(
            {
                "accepted": True,
                "reason": "ok",
                "promise_id": 3,
                "max_date": "2026-11-23",
                "amount_due": "200.72",
            }
        )
        verified = await client.verify_identity("Frank Fischer", dt.date(1988, 3, 14), "4417")
        promise = await client.record_promise_to_pay(dt.date(2026, 11, 13), Decimal("200.72"))
        assert verified.data is not None and verified.data.verified
        assert promise.data is not None and promise.data.promise_id == 3
        (_, verify_args), (_, promise_args) = state.calls
        assert verify_args == {"full_name": "Frank Fischer", "dob": "1988-03-14", "loan_last4": "4417"}
        assert promise_args == {"promise_date": "2026-11-13", "amount": "200.72"}
        assert all("customer_id" not in args for _, args in state.calls)


async def test_one_method_per_tool_and_the_outcome_writer() -> None:
    async with open_fake() as (state, client):
        for tool in [*TOOL_MODELS, "record_call_outcome"]:
            assert callable(getattr(client, tool)), tool
            assert callable(getattr(GatewayPort, tool)), tool
        state.replies["record_call_outcome"] = ok({"interaction_id": CALL_ID})
        outcome = CallOutcome(
            call_id=CALL_ID,
            direction=Direction.outbound,
            campaign=Campaign.missed_payment,
            contact_result=ContactResult.no_answer,
            versions=Versions(prompt="p1", policy="k1", model="llamacpp/x"),
        )
        result = await client.record_call_outcome(outcome)
        assert result.data is not None and result.data.interaction_id == CALL_ID
        assert state.calls[0][0] == "record_call_outcome" and state.calls[0][1]["call_id"] == CALL_ID


async def test_escalate_sends_the_reason_value() -> None:
    async with open_fake() as (state, client):
        state.replies["escalate"] = ok({"task_id": 9, "human_available": True, "transfer_extension": "200"})
        result = await client.escalate(EscalationReason.dispute, "Caller disputes the late fee.")
        assert result.data is not None and result.data.transfer_extension == "200"
        assert state.calls[0][1] == {"reason": "dispute", "summary": "Caller disputes the late fee."}


async def test_a_broken_reply_becomes_upstream_error_without_leaking(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with open_fake() as (state, client):
        state.broken = True
        with caplog.at_level("DEBUG"):
            result = await client.get_loan_status()
        assert result.error is not None and result.error.code == "upstream_error"
        assert TOKEN not in caplog.text and "not json" not in caplog.text


async def test_a_schema_mismatch_becomes_upstream_error() -> None:
    async with open_fake() as (state, client):
        state.replies["lookup_by_phone"] = ok({"unexpected": 1})
        result = await client.lookup_by_phone()
        assert result.error is not None and result.error.code == "upstream_error"


async def test_calling_before_opening_is_a_programming_error() -> None:
    client = GatewayClient(GATEWAY_URL, SecretStr(TOKEN))
    with pytest.raises(RuntimeError):
        await client.lookup_by_phone()
    assert TOKEN not in repr(client) and "***" in repr(client)


# --------------------------------------------------------------------------- session API


def _meta() -> CallMetadata:
    return CallMetadata(
        call_id=CALL_ID, direction=Direction.outbound, campaign=Campaign.missed_payment, loan_id=17
    )


def test_urls() -> None:
    assert session_base_url("http://gateway:8002/mcp") == "http://gateway:8002"
    assert session_base_url("http://gateway:8002/mcp/") == "http://gateway:8002"
    assert mcp_url("http://gateway:8002") == "http://gateway:8002/mcp"


async def test_open_session_posts_with_the_worker_credential() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"session_token": TOKEN, "expires_at": "2026-11-09T15:00:00Z"})

    token = await open_session(
        GATEWAY_URL, SecretStr("worker-secret"), _meta(), transport=httpx.MockTransport(handler)
    )
    assert token.get_secret_value() == TOKEN and TOKEN not in repr(token)
    (request,) = seen
    assert (request.method, request.url.path) == ("POST", "/sessions")
    assert request.headers["authorization"] == "Bearer worker-secret"
    assert json.loads(request.content) == {
        "call_id": CALL_ID,
        "direction": "outbound",
        "campaign": "missed_payment",
        "loan_id": 17,
    }


@pytest.mark.parametrize("status", [401, 429, 502])
async def test_open_session_failure_is_a_clean_error(status: int, caplog: pytest.LogCaptureFixture) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(status, json={"detail": "secret detail"}))
    with caplog.at_level("DEBUG"), pytest.raises(GatewayUnavailable) as err:
        await open_session(GATEWAY_URL, SecretStr("worker-secret"), _meta(), transport=transport)
    assert str(status) in str(err.value)
    assert "worker-secret" not in str(err.value) + caplog.text and "secret detail" not in caplog.text


async def test_close_session_is_idempotent_and_never_raises() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(500) if len(calls) == 2 else httpx.Response(204)

    transport = httpx.MockTransport(handler)
    await close_session(GATEWAY_URL, SecretStr("w"), CALL_ID, transport=transport)
    await close_session(GATEWAY_URL, SecretStr("w"), CALL_ID, transport=transport)  # gets a 500
    assert calls == [("DELETE", f"/sessions/{CALL_ID}")] * 2
