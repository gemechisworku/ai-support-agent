"""Service-scope access (`/service/*`, NFR-SEC): the shared credential check and the audit wrapper.

Service routes (the console's KB browse API, T-GW-12; the scheduler's API, T-GW-10) authenticate with
`Authorization: Bearer $GATEWAY_SERVICE_TOKEN` and are audited like tools (GW-R8): every request —
allowed, denied or failed — writes one audit row with `call_id="service"` before the response is
returned, and fails closed (503) if that write fails.

    router = APIRouter(prefix="/service")

    @router.get("/thing")
    async def thing(request: Request, ...) -> Model:
        return await service_call(request, "thing", {"arg": arg}, lambda: produce(...))

- no credential → 401; a credential that is not the service token (worker or session token) → 403;
  more than `credential_per_minute` requests → 429.
"""

import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPBearer

from supportagent.gateway.audit import AuditUnavailable, Decision
from supportagent.gateway.tools import Gateway

SERVICE_CALL_ID = "service"
_bearer = HTTPBearer(auto_error=False)


def gateway_of(request: Request) -> Gateway:
    gw: Gateway = request.app.state.gateway
    return gw


async def _service_denial(request: Request) -> HTTPException | None:
    """The HTTP error for this request's credential, or None if it is the service token."""
    gw = gateway_of(request)
    creds = await _bearer(request)
    if creds is None:
        return HTTPException(status.HTTP_401_UNAUTHORIZED, detail="service credential required")
    if not secrets.compare_digest(creds.credentials, gw.config.service_token):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail="forbidden")
    if not gw.limiter.allow("credential:service", gw.config.limits.credential_per_minute):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")
    return None


async def _audit(gw: Gateway, tool: str, args: Any, decision: Decision, started: float) -> None:
    try:
        await gw.audit.write(
            call_id=SERVICE_CALL_ID,
            tool=tool,
            args=args,
            customer_id=None,
            decision=decision,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
    except AuditUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="audit_unavailable") from exc


async def service_call[T](request: Request, tool: str, args: Any, produce: Callable[[], Awaitable[T]]) -> T:
    """Authenticate a service request, run `produce`, and audit the outcome (fail closed)."""
    gw = gateway_of(request)
    started = time.perf_counter()
    denial = await _service_denial(request)
    if denial is not None:
        await _audit(gw, tool, args, "deny", started)
        raise denial
    try:
        result = await produce()
    except HTTPException as exc:
        await _audit(gw, tool, args, "deny" if exc.status_code < 500 else "error", started)
        raise
    except Exception as exc:
        await _audit(gw, tool, args, "error", started)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="upstream_error") from exc
    await _audit(gw, tool, args, "allow", started)
    return result
