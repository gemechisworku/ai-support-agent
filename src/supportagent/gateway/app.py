"""The gateway service (port 8002): session API, MCP tools at `/mcp` (T-GW-09), service API (T-GW-10).

    uvicorn supportagent.gateway.app:app --host 0.0.0.0 --port 8002

`POST /sessions` and `DELETE /sessions/{call_id}` are called by the agent worker's code with
`Authorization: Bearer $GATEWAY_WORKER_TOKEN`; the returned session token authorises MCP tool calls.
"""

import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from supportagent.common.settings import get_settings
from supportagent.contracts.gateway import SessionCreate, SessionCreated
from supportagent.gateway.bank_client import BankError, HttpBankClient
from supportagent.gateway.config import SESSION_MAX_TTL, GatewayConfig
from supportagent.gateway.tools import Gateway

_bearer = HTTPBearer(auto_error=False)


def default_gateway() -> Gateway:
    settings = get_settings()
    config = GatewayConfig.from_settings(settings)
    bank = HttpBankClient(settings.services.bank_api_url, config.service_token)
    return Gateway(bank=bank, config=config)


def _gateway(request: Request) -> Gateway:
    gw: Gateway = request.app.state.gateway
    return gw


def require_worker(
    request: Request, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> None:
    gw = _gateway(request)
    if creds is None or not secrets.compare_digest(creds.credentials, gw.config.worker_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="worker credential required")
    if not gw.limiter.allow("credential:worker", gw.config.limits.credential_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")


def create_app(gateway: Gateway | None = None) -> FastAPI:
    app = FastAPI(title="Agent data gateway", version="1.0")
    app.state.gateway = gateway or default_gateway()
    worker = [Depends(require_worker)]

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/sessions", response_model=SessionCreated, dependencies=worker, tags=["sessions"])
    async def create_session(body: SessionCreate, request: Request) -> SessionCreated:
        gw = _gateway(request)
        try:
            session = await gw.store.create(body, gw.bank)
        except BankError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="bank unavailable") from exc
        return SessionCreated(session_token=session.token, expires_at=session.created_at + SESSION_MAX_TTL)

    @app.delete("/sessions/{call_id}", status_code=204, dependencies=worker, tags=["sessions"])
    async def close_session(call_id: str, request: Request) -> Response:
        _gateway(request).store.close(call_id)
        return Response(status_code=204)

    return app
