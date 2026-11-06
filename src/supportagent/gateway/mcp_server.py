"""The MCP surface (GW-R1): the allow-listed tools over streamable HTTP at `/mcp`.

Built on the SDK's low-level `Server` so each tool's input schema is exactly its Pydantic model
(`additionalProperties: false`) and every call goes through `tools.run_tool` — the same pipeline the
unit tests exercise. The session token is read from the HTTP `Authorization: Bearer …` header
(`_session_token`); it is never a tool argument. Results are the `ToolResult` envelope, returned both
as structured content and as JSON text; MCP-level `isError` stays false so small models see one
consistent shape (plan, "Error style").
"""

import json
from collections.abc import Mapping
from typing import Any

import mcp_types as types
from mcp.server import Server
from mcp.server.context import ServerRequestContext

from supportagent.gateway.tools import REGISTRY, Gateway, ToolRegistry, run_tool

SERVER_NAME = "supportagent-gateway"

DESCRIPTIONS = {
    "lookup_by_phone": "Whether the line belongs to a known customer; "
    "on outbound calls also their first name.",
    "verify_identity": "Verify the caller (full name, date of birth, last 4 digits of a loan number). "
    "Two failed attempts lock the session.",
    "search_policy": "Search the bank's policy documents. Answer only from the returned text; "
    "if low_confidence is true, offer a follow-up instead of answering.",
    "get_loan_status": "Verified caller only: balance, overdue amount, next due date and amount, "
    "days past due.",
    "get_recent_transactions": "Verified caller only: the last 5 payments; pending ones are flagged.",
    "get_interaction_history": "Verified caller only: recent calls, open promises and open tasks.",
    "record_promise_to_pay": "Verified caller only: record a promise to pay (date within the policy window, "
    "amount up to what is due).",
    "request_callback": "Schedule a callback at the caller's preferred time.",
    "escalate": "Hand the call to a human (dispute, hardship, complaint, legal, request for a human, ...).",
    "set_contact_flag": "Verified caller only: record a dispute, a request to stop calling, "
    "or a preferred window.",
    "record_call_outcome": "Worker only: the call's final structured outcome; closes the session.",
}


def strict_schema(model: Any) -> dict[str, Any]:
    schema: dict[str, Any] = model.model_json_schema()
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema["additionalProperties"] = False
    return schema


def _session_token(ctx: ServerRequestContext[Any, Any]) -> str | None:
    """The bearer token of the HTTP request carrying this MCP message (isolated: SDK APIs vary)."""
    headers: Mapping[str, str] | None = getattr(ctx.request, "headers", None)
    if not headers:
        return None
    value = headers.get("authorization") or headers.get("Authorization") or ""
    scheme, _, token = value.partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


def build_mcp_server(gateway: Gateway, registry: ToolRegistry = REGISTRY) -> Server[Any]:
    tools = [
        types.Tool(
            name=spec.name,
            description=DESCRIPTIONS.get(spec.name, spec.name),
            input_schema=strict_schema(spec.input_model),
        )
        for spec in registry.values()
    ]

    async def on_list_tools(
        ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(
        ctx: ServerRequestContext[Any, Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        result = await run_tool(
            gateway, params.name, _session_token(ctx), dict(params.arguments or {}), registry
        )
        payload = result.model_dump(mode="json")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload))],
            structured_content=payload,
            is_error=False,
        )

    return Server(SERVER_NAME, version="1.0", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
