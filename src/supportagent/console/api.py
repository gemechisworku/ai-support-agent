"""Console API: the backend for the Next.js console (T-CON-09, CON-R1/R7/R11).

    uvicorn --factory supportagent.console.api:create_app --port 8004

The Next.js server is its only client. Every route except `/healthz` needs
`Authorization: Bearer <CONSOLE_API_TOKEN>`. Routes are thin: they call the typed upstream clients
(`console.clients`: bank API, gateway service API, scheduler) and return `console.views` models, which
mask identifiers before anything leaves Python. The OpenAPI document of this app is the contract the
web app's TypeScript types are generated from (`make console-types`).

Errors: an upstream 404 becomes 404; any other upstream failure becomes `502 {"detail": msg}` with a
message that is safe to show. Tests pass a `Clients` bundle backed by `httpx.MockTransport`.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from supportagent.console import clients as cl
from supportagent.console import views as v
from supportagent.contracts.kb import KbDocumentDetail, KbDocumentSummary, PolicyQuery, PolicySearchResult

BUCKETS = ("current", "1-30", "31-60", "61-90", "90+")
MAX_LIMIT = 200
TRANSACTIONS_PER_LOAN = 20

_bearer = HTTPBearer(auto_error=False)
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0)]


def _operation_id(route: APIRoute) -> str:
    """Stable, readable operation ids (the route function name) for the generated TS client."""
    return route.name


def create_app(clients: cl.Clients | None = None, token: str | None = None) -> FastAPI:
    """The console API. `clients` / `token` default to the ones built from settings."""
    expected = token if token is not None else _token_from_settings()
    get_clients: Callable[[], cl.Clients] = (lambda: clients) if clients is not None else cl.get_clients

    def require_token(
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> None:
        if creds is None or not hmac.compare_digest(creds.credentials.encode(), expected.encode()):
            raise HTTPException(401, "missing or invalid console API token")

    app = FastAPI(
        title="Support agent console API",
        version="1",
        description="Backend for the Next.js operations console (specs/11-console). Local POC only.",
        generate_unique_id_function=_operation_id,
    )

    @app.exception_handler(cl.ConsoleApiError)
    def _upstream_error(_: Request, exc: cl.ConsoleApiError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404 if exc.status == 404 else 502)

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

    # ------------------------------------------------------------------ overview

    @api.get("/overview", tags=["explorer"])
    def get_overview() -> v.Overview:
        c = get_clients()
        by_bucket = {bucket: c.bank.list_loans(bucket=bucket, limit=1).total for bucket in BUCKETS}
        return v.Overview(
            customers=c.bank.search_customers(limit=1).total,
            loans=c.bank.list_loans(limit=1).total,
            loans_by_bucket=by_bucket,
            kb_documents=len(c.gateway.kb_documents()),
            personas=len(c.bank.personas()),
        )

    # ------------------------------------------------------------------ customers and loans (CON-R8)

    @api.get("/customers", tags=["explorer"])
    def search_customers(
        q: str = "",
        segment: str | None = None,
        bucket: str | None = None,
        limit: Limit = 25,
        offset: Offset = 0,
    ) -> v.Paged[v.CustomerRow]:
        page = get_clients().bank.search_customers(q.strip(), segment, bucket, limit, offset)
        return v.Paged[v.CustomerRow](
            items=[v.customer_row(s) for s in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )

    @api.get("/customers/{customer_id}", tags=["explorer"])
    def get_customer(customer_id: int) -> v.CustomerDetail:
        bank = get_clients().bank
        customer = bank.customer(customer_id)
        loans = []
        for loan in bank.customer_loans(customer_id):
            loans.append(
                v.loan_detail(
                    bank.loan_status(loan.id),
                    bank.installments(loan.id),
                    bank.transactions(loan.repayment_account_id, loan.id, TRANSACTIONS_PER_LOAN),
                )
            )
        history = bank.customer_history(customer_id)
        return v.CustomerDetail(
            profile=v.customer_profile(customer),
            preferences=v.preferences_view(bank.contact_preferences(customer_id)),
            loans=loans,
            interactions=[v.interaction_view(i) for i in history.interactions],
            open_promises=[v.promise_view(p) for p in history.open_promises],
            open_tasks=[v.task_view(t) for t in history.open_tasks],
        )

    @api.get("/loans", tags=["explorer"])
    def list_loans(
        bucket: str | None = None,
        product: str | None = None,
        limit: Limit = 25,
        offset: Offset = 0,
    ) -> v.Paged[v.LoanRow]:
        page = get_clients().bank.list_loans(bucket, product, limit, offset)
        return v.Paged[v.LoanRow](
            items=[v.loan_row(s) for s in page.items], total=page.total, limit=page.limit, offset=page.offset
        )

    # ------------------------------------------------------------------ policies (CON-R9)

    @api.get("/policies", tags=["explorer"])
    def list_policies() -> list[KbDocumentSummary]:
        return get_clients().gateway.kb_documents()

    @api.get("/policies/{doc_id}", tags=["explorer"])
    def get_policy(doc_id: str, version: str | None = None) -> KbDocumentDetail:
        return get_clients().gateway.kb_document(doc_id, version)

    @api.post("/policies/search", tags=["explorer"])
    def search_policies(query: PolicyQuery) -> PolicySearchResult:
        return get_clients().gateway.kb_search(query)

    # ------------------------------------------------------------------ personas and audit (CON-R10)

    @api.get("/personas", tags=["explorer"])
    def list_personas() -> list[v.PersonaView]:
        return [v.persona_view(p) for p in get_clients().bank.personas()]

    @api.get("/audit/{call_id}", tags=["explorer"])
    def get_audit(call_id: str) -> list[v.AuditView]:
        rows = get_clients().bank.audit(call_id, limit=MAX_LIMIT).items
        return [v.audit_view(r) for r in sorted(rows, key=lambda r: (r.ts, r.id))]

    app.include_router(api)
    return app


def _token_from_settings() -> str:
    from supportagent.common.settings import get_settings

    secret = get_settings().services.console_api_token
    if secret is None or not secret.get_secret_value():
        raise RuntimeError("CONSOLE_API_TOKEN is not set: add it to .env (see .env.example)")
    return secret.get_secret_value()
