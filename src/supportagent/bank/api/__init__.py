"""The mock core bank's internal API (specs/02-mock-bank). Run:

    uvicorn supportagent.bank.api:app --host 0.0.0.0 --port 8001

Every route except `/healthz` requires `X-Service-Token: $GATEWAY_SERVICE_TOKEN`. Responses are
unmasked (trusted-internal); only the gateway and the scheduler call this API.
"""

from fastapi import Depends, FastAPI

from supportagent.bank.api import routes_collections, routes_explorer, routes_ops, routes_read, routes_write
from supportagent.bank.api.deps import require_service_token


def create_app() -> FastAPI:
    app = FastAPI(title="Mock core bank (internal)", version="1.0")
    protected = [Depends(require_service_token)]
    # explorer first: its fixed paths (/customers/search, …) must match before /customers/{customer_id}
    app.include_router(routes_explorer.router, dependencies=protected)
    app.include_router(routes_read.router, dependencies=protected)
    app.include_router(routes_collections.router, dependencies=protected)
    app.include_router(routes_write.router, dependencies=protected)
    app.include_router(routes_ops.router, dependencies=protected, tags=["ops"])

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
