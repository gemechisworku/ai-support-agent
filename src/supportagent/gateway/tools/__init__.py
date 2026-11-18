"""The tool pipeline: every MCP tool runs through `run_tool`, so every tool gets the same guardrails.

    resolve session → scope → rate limit → validate input → handler → ToolResult envelope → audit

- **Session:** the bearer token from the request (never a tool argument). Missing/unknown/expired →
  `forbidden`; closed → `session_closed`.
- **Scope:** `unverified` (identity tools; refused once locked), `verified` (needs a bound customer),
  `any` (policy search, callbacks, escalation — also on locked sessions), `worker` (the outcome write;
  never registered as an LLM tool by the agent).
- **Validation:** Pydantic models with `extra="forbid"`; error messages name fields, never echo values.
- **Rate limits:** per session 20/min and 150 per call, `search_policy` 10/min (`rate_limited`).
- **Handlers** receive the session (with the bound customer) and the typed input and return the typed
  output, or raise `ToolDenied(code)`. Bank failures become `upstream_error`.
- **Audit:** every request — allowed, denied or failed, even without a valid token — writes one row
  before the result is returned; if that write fails the caller gets `audit_unavailable` and no data.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

from supportagent.contracts.gateway import ToolErrorCode, ToolResult
from supportagent.gateway.audit import AuditUnavailable, AuditWriter, Decision
from supportagent.gateway.bank_client import BankClient, BankError
from supportagent.gateway.config import GatewayConfig
from supportagent.gateway.ratelimit import RateLimiter
from supportagent.gateway.retriever import Retriever
from supportagent.gateway.session import Session, SessionStore

if TYPE_CHECKING:
    from supportagent.gateway.kb_browse import KbBrowser

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
    limiter: RateLimiter = field(default_factory=RateLimiter)
    retriever: Retriever | None = None
    kb_browser: "KbBrowser | None" = None  # console KB browse API (T-GW-12)
    audit: AuditWriter = field(init=False)

    def __post_init__(self) -> None:
        self.audit = AuditWriter(self.bank, self.config.audit_salt)


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


UNAUTHENTICATED = "unauthenticated"  # audit call_id when the request carried no valid session token


def _decision(result: ToolResult[Any]) -> Decision:
    if result.ok:
        return "allow"
    assert result.error is not None
    return "error" if result.error.code == "upstream_error" else "deny"


def rate_limited(gateway: Gateway, session: Session, tool: str) -> bool:
    limits = gateway.config.limits
    session.tool_calls += 1
    if session.tool_calls > limits.session_per_call:
        return True
    if not gateway.limiter.allow(f"session:{session.token}", limits.session_per_minute):
        return True
    return tool == "search_policy" and not gateway.limiter.allow(
        f"search:{session.token}", limits.search_per_minute
    )


async def _execute(
    gateway: Gateway, spec: ToolSpec | None, name: str, session: Session | None, args: dict[str, Any] | None
) -> ToolResult[Any]:
    if spec is None:
        return ToolResult.failure("invalid_input", f"unknown tool {name}")
    if session is None:
        return ToolResult.failure("forbidden")
    denied = check_scope(session, spec.scope)
    if denied is not None:
        return ToolResult.failure(denied)
    if rate_limited(gateway, session, name):
        return ToolResult.failure("rate_limited")
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
    return ToolResult.success(output)


async def run_tool(
    gateway: Gateway,
    name: str,
    token: str | None,
    args: dict[str, Any] | None,
    registry: ToolRegistry = REGISTRY,
) -> ToolResult[Any]:
    """Run one tool call through the full pipeline and audit it (fail closed)."""
    started = time.perf_counter()
    session = gateway.store.resolve(token)
    result = await _execute(gateway, registry.get(name), name, session, args)
    try:
        await gateway.audit.write(
            call_id=session.call_id if session else UNAUTHENTICATED,
            tool=name,
            args=args,
            customer_id=session.bound_customer_id if session else None,
            decision=_decision(result),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
    except AuditUnavailable:
        return ToolResult.failure("audit_unavailable")
    return result


# Importing the tool modules registers their tools in REGISTRY.
from supportagent.gateway.tools import account, identity, policy, writes  # noqa: E402, F401
