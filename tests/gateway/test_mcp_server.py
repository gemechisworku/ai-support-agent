"""T-GW-09 · GW-R1: the MCP surface, through a real MCP client over streamable HTTP.

The gateway app (over `FakeBank` and a fake retriever) is served by uvicorn on a free local port in a
background thread; the SDK client connects with an `Authorization: Bearer <session token>` header.
"""

import json
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import httpx2
import pytest
import uvicorn
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from supportagent.gateway.app import create_app
from supportagent.gateway.config import GatewayConfig
from supportagent.gateway.tools import Gateway
from tests.gateway.fakes import FRANK, FakeBank, FakeRetriever, chunk

CONFIG = GatewayConfig(worker_token="worker-tok", service_token="service-tok", audit_salt="salt")
SPEC_TOOLS = {
    "lookup_by_phone", "verify_identity", "search_policy", "get_loan_status", "get_recent_transactions",
    "get_interaction_history", "record_promise_to_pay", "request_callback", "escalate", "set_contact_flag",
    "record_call_outcome",
}  # fmt: skip


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def served() -> Iterator[tuple[str, Gateway]]:
    gateway = Gateway(bank=FakeBank(), config=CONFIG, retriever=FakeRetriever(chunks=[chunk()]))
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(gateway), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/healthz", timeout=0.5).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.05)
    yield base, gateway
    server.should_exit = True
    thread.join(timeout=5)


def _session(base: str, loan_id: int = FRANK.loan_id) -> str:
    body = {
        "call_id": str(uuid.uuid4()),
        "direction": "outbound",
        "campaign": "missed_payment",
        "loan_id": loan_id,
    }
    resp = httpx.post(
        f"{base}/sessions", json=body, headers={"Authorization": f"Bearer {CONFIG.worker_token}"}
    )
    resp.raise_for_status()
    return str(resp.json()["session_token"])


async def _call(base: str, token: str | None, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(headers=headers) as http, Client(_transport(base, http)) as client:
        result = await client.call_tool(tool, args)
    assert result.structured_content is not None
    assert json.loads(result.content[0].text) == result.structured_content  # type: ignore[union-attr]
    return dict(result.structured_content)


def _transport(base: str, http: httpx2.AsyncClient) -> Any:
    return streamable_http_client(f"{base}/mcp", http_client=http)


async def test_lists_exactly_the_spec_tools_with_strict_schemas(served: tuple[str, Gateway]) -> None:
    base, _ = served
    async with httpx2.AsyncClient() as http, Client(_transport(base, http)) as client:
        listed = await client.list_tools()
    assert {t.name for t in listed.tools} == SPEC_TOOLS
    for tool in listed.tools:
        assert tool.input_schema.get("additionalProperties") is False, tool.name
        assert (
            "customer_id" not in tool.input_schema.get("properties", {}) or tool.name == "record_call_outcome"
        )


async def test_tool_calls_with_a_header_token(served: tuple[str, Gateway]) -> None:
    base, _ = served
    token = _session(base)
    policy = await _call(base, token, "search_policy", {"query": "late fee"})
    assert policy["ok"] and policy["data"]["chunks"][0]["doc_id"] == "late-fees-penalty-interest"
    claim = {"full_name": "Frank Fischer", "dob": FRANK.dob.isoformat(), "loan_last4": FRANK.loan_last4}
    verified = await _call(base, token, "verify_identity", claim)
    assert verified["data"]["verified"] is True
    status = await _call(base, token, "get_loan_status", {})
    assert status["data"]["loans"][0]["days_past_due"] == 5


async def test_missing_or_wrong_token_is_a_forbidden_envelope(served: tuple[str, Gateway]) -> None:
    base, _ = served
    for token in (None, "not-a-session"):
        result = await _call(base, token, "search_policy", {"query": "late fee"})
        assert result == {"ok": False, "data": None, "error": {"code": "forbidden", "message": ""}}


async def test_customer_id_argument_is_rejected(served: tuple[str, Gateway]) -> None:
    base, _ = served
    token = _session(base)
    result = await _call(base, token, "search_policy", {"query": "fees", "customer_id": 9003})
    assert result["error"]["code"] == "invalid_input"
