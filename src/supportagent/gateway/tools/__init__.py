"""The tool pipeline: every MCP tool runs through `run_tool`, so every tool gets the same guardrails.

    resolve session → scope → rate limit → validate input → handler → ToolResult envelope → audit

- **Session:** the bearer token from the request (never a tool argument). Missing/unknown/expired →
  `forbidden`; closed → `session_closed`.
- **Scope:** `unverified` (identity tools; refused once locked), `verified` (needs a bound customer),
  `any` (policy search, callbacks, escalation — also on locked sessions), `worker` (the outcome write;
  never registered as an LLM tool by the agent).
- **Validation:** Pydantic models with `extra="forbid"`; error messages name fields, never echo values.
- **Handlers** receive the session (with the bound customer) and the typed input and return the typed
  output, or raise `ToolDenied(code)`. Bank failures become `upstream_error`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from supportagent.contracts.gateway import ToolErrorCode, ToolResult
from supportagent.gateway.bank_client import BankClient, BankError
from supportagent.gateway.config import GatewayConfig
from supportagent.gateway.session import Session, SessionStore

Scope = Literal["unverified", "verified", "any", "worker"]


class ToolDenied(Exception):
    def __init__(self, code: ToolErrorCode, message: str = "") -> None:
        super().__init__(message or code)
        self.code: ToolErrorCode = code
        self.message = message


@dataclass
class Gateway:
    """Everything a tool may use. Built once per process (app.py) or per test."""

    bank: BankClient
    config: GatewayConfig
    store: SessionStore = field(default_factory=SessionStore)


@dataclass
class ToolContext:
    gateway: Gateway
    session: Session

    @property
    def customer_id(self) -> int:
        """The bound customer. Only reachable from `verified` tools, which the pipeline guards."""
        if self.session.bound_customer_id is None:
            raise ToolDenied("not_verified")
        return self.session.bound_customer_id


Handler = Callable[[ToolContext, Any], Awaitable[BaseModel]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    scope: Scope
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Handler


class ToolRegistry(dict[str, ToolSpec]):
    def tool(
        self, name: str, *, scope: Scope, input_model: type[BaseModel], output_model: type[BaseModel]
    ) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            if name in self:
                raise ValueError(f"tool {name} registered twice")
            self[name] = ToolSpec(name, scope, input_model, output_model, handler)
            return handler

        return register


REGISTRY = ToolRegistry()
gateway_tool = REGISTRY.tool


def check_scope(session: Session, scope: Scope) -> ToolErrorCode | None:
    if session.closed:
        return "session_closed"
    if scope in ("any", "worker"):
        return None
    if session.locked:
        return "locked"
    if scope == "verified" and not session.verified:
        return "not_verified"
    return None


def _validation_message(exc: ValidationError) -> str:
    """Field names and error types only — never the (possibly injected or personal) input values."""
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['type']}" for e in exc.errors())


async def run_tool(
    gateway: Gateway,
    name: str,
    token: str | None,
    args: dict[str, Any] | None,
    registry: ToolRegistry = REGISTRY,
) -> ToolResult[Any]:
    spec = registry.get(name)
    if spec is None:
        return ToolResult.failure("invalid_input", f"unknown tool {name}")
    session = gateway.store.resolve(token)
    if session is None:
        return ToolResult.failure("forbidden")
    denied = check_scope(session, spec.scope)
    if denied is not None:
        return ToolResult.failure(denied)
    try:
        payload = spec.input_model.model_validate(args or {})
    except ValidationError as exc:
        return ToolResult.failure("invalid_input", _validation_message(exc))
    try:
        output = await spec.handler(ToolContext(gateway, session), payload)
    except ToolDenied as d:
        return ToolResult.failure(d.code, d.message)
    except BankError:
        return ToolResult.failure("upstream_error")
    session.tool_calls += 1
    return ToolResult.success(output)
