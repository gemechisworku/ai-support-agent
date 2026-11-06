"""The mock core bank's internal API (specs/02-mock-bank). Run:

    uvicorn supportagent.bank.api:app --host 0.0.0.0 --port 8001

Every route except `/healthz` requires `X-Service-Token: $GATEWAY_SERVICE_TOKEN`. Responses are
unmasked (trusted-internal); only the gateway and the scheduler call this API.
"""

from fastapi import Depends, FastAPI

from supportagent.bank.api import routes_read
from supportagent.bank.api.deps import require_service_token


def create_app() -> FastAPI:
    app = FastAPI(title="Mock core bank (internal)", version="1.0")
    protected = [Depends(require_service_token)]
    app.include_router(routes_read.router, dependencies=protected)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
