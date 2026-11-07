"""The gateway service (port 8002): session API, MCP tools at `/mcp`, service API (T-GW-10).

    uvicorn --factory supportagent.gateway.app:create_app --host 0.0.0.0 --port 8002

- `POST /sessions`, `DELETE /sessions/{call_id}` — called by the agent worker's code with
  `Authorization: Bearer $GATEWAY_WORKER_TOKEN`; the returned session token authorises MCP calls.
- `/mcp` — MCP streamable HTTP (stateless, JSON responses), `Authorization: Bearer <session_token>`.
- `/service/kb/*` — read-only knowledge-base browse and search for the console (T-GW-12),
  `Authorization: Bearer $GATEWAY_SERVICE_TOKEN` (shared service dependency in `auth.py`).
- `/healthz`.
"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from supportagent.common.db import kb_engine
from supportagent.common.settings import get_settings
from supportagent.contracts.gateway import SessionCreate, SessionCreated
from supportagent.gateway.bank_client import BankError, HttpBankClient
from supportagent.gateway.config import SESSION_MAX_TTL, GatewayConfig
from supportagent.gateway.kb_browse import SqlKbBrowser
from supportagent.gateway.kb_browse import router as kb_browse_router
from supportagent.gateway.mcp_server import build_mcp_server
from supportagent.gateway.retriever import KbRetriever
from supportagent.gateway.tools import Gateway
from supportagent.kb.embed import get_embedder

_bearer = HTTPBearer(auto_error=False)


def default_gateway() -> Gateway:
    settings = get_settings()
    config = GatewayConfig.from_settings(settings)
    bank = HttpBankClient(settings.services.bank_api_url, config.service_token)
    retriever = KbRetriever.from_engine(kb_engine(), get_embedder(settings))
    return Gateway(
        bank=bank, config=config, retriever=retriever, kb_browser=SqlKbBrowser.from_engine(kb_engine())
    )


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
    gw = gateway or default_gateway()
    mcp = build_mcp_server(gw)
    # Stateless JSON mode: every MCP request carries its own bearer token, so no MCP session state is
    # needed. host="0.0.0.0" turns off the SDK's localhost-only Host check, because inside compose
    # the agent calls `gateway:8002`; the gateway is only exposed on the internal network.
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        host="0.0.0.0",  # noqa: S104
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Agent data gateway", version="1.0", lifespan=lifespan)
    app.state.gateway = gw
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

    app.include_router(kb_browse_router)
    app.mount("/", mcp_app)  # after the routes above, so /healthz and /sessions match first
    return app
