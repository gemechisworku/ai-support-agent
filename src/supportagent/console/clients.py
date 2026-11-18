"""Typed HTTP clients the console uses for every byte of data it shows (CON-R1).

The console has no database access and imports no bank/kb/gateway code (import-linter). It talks to:

- the bank API (`BankOpsClient`, header `X-Service-Token`), models from `contracts/bank.py`;
- the gateway service API (`GatewayServiceClient`, `Authorization: Bearer <service token>`), models
  from `contracts/kb.py`;
- the scheduler (`SchedulerClient`), a stub until T-SCHED-06 (M3) provides the trigger API.

Every HTTP or validation failure becomes a `ConsoleApiError`, which the console API maps to 404/502.
The console API gets clients through `get_clients()`; tests inject `httpx.MockTransport` clients.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from supportagent.contracts import bank as b
from supportagent.contracts.kb import KbDocumentDetail, KbDocumentSummary, PolicyQuery, PolicySearchResult

T = TypeVar("T")
TIMEOUT_S = 10.0


class ConsoleApiError(Exception):
    """A request the console could not complete; the message is safe to show to the operator.

    `status` is the upstream HTTP status when there was one (the console API maps 404 to 404).
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _Http:
    def __init__(self, base_url: str, headers: dict[str, str], transport: httpx.BaseTransport | None) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=TIMEOUT_S, transport=transport
        )
        self.base_url = base_url

    def _request(self, method: str, path: str, model: Any, **kwargs: Any) -> Any:
        params = kwargs.pop("params", None)
        if params is not None:
            kwargs["params"] = {k: _param(v) for k, v in params.items() if v is not None and v != ""}
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ConsoleApiError(f"{self.base_url} is not reachable ({exc.__class__.__name__})") from exc
        if response.status_code >= 400:
            raise ConsoleApiError(
                f"{method} {path} failed: HTTP {response.status_code} {_detail(response)}",
                status=response.status_code,
            )
        try:
            return TypeAdapter(model).validate_python(response.json())
        except (ValueError, ValidationError) as exc:
            raise ConsoleApiError(f"{method} {path} returned an unexpected response") from exc

    def get(self, path: str, model: Any, **params: Any) -> Any:
        return self._request("GET", path, model, params=params)


def _param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return str(value)


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    return f"({detail})" if isinstance(detail, str) else ""


class BankOpsClient(_Http):
    """The bank API: read endpoints (BANK-R4/R5), ops endpoints (T-BANK-07), explorer (T-BANK-09)."""

    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(base_url, {"X-Service-Token": token}, transport)

    # explorer (T-BANK-09)
    def search_customers(
        self,
        q: str = "",
        segment: str | None = None,
        bucket: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> b.Page[b.CustomerSummary]:
        return self.get(
            "/customers/search",
            b.Page[b.CustomerSummary],
            q=q,
            segment=segment,
            bucket=bucket,
            limit=limit,
            offset=offset,
        )

    def list_loans(
        self, bucket: str | None = None, product: str | None = None, limit: int = 50, offset: int = 0
    ) -> b.Page[b.LoanStatus]:
        return self.get(
            "/loans", b.Page[b.LoanStatus], bucket=bucket, product=product, limit=limit, offset=offset
        )

    def contact_preferences(self, customer_id: int) -> b.ContactPreferences:
        return self.get(f"/customers/{customer_id}/contact-preferences", b.ContactPreferences)

    def personas(self) -> list[b.PersonaCard]:
        return self.get("/personas", list[b.PersonaCard])

    # read endpoints
    def customer(self, customer_id: int) -> b.Customer:
        return self.get(f"/customers/{customer_id}", b.Customer)

    def customer_loans(self, customer_id: int) -> list[b.Loan]:
        return self.get(f"/customers/{customer_id}/loans", list[b.Loan])

    def customer_history(self, customer_id: int, limit: int = 20) -> b.InteractionHistory:
        return self.get(f"/customers/{customer_id}/interactions", b.InteractionHistory, limit=limit)

    def loan_status(self, loan_id: int) -> b.LoanStatus:
        return self.get(f"/loans/{loan_id}", b.LoanStatus)

    def installments(self, loan_id: int) -> list[b.Installment]:
        return self.get(f"/loans/{loan_id}/installments", list[b.Installment])

    def transactions(
        self, account_id: int, loan_id: int | None = None, limit: int = 20
    ) -> list[b.Transaction]:
        return self.get(
            f"/accounts/{account_id}/transactions", list[b.Transaction], loan_id=loan_id, limit=limit
        )

    # operations (T-BANK-07)
    def interactions(self, limit: int = 50, offset: int = 0, **filters: Any) -> b.Page[b.Interaction]:
        return self.get("/interactions", b.Page[b.Interaction], limit=limit, offset=offset, **filters)

    def call_detail(self, call_id: str) -> b.CallDetail:
        return self.get(f"/interactions/{call_id}", b.CallDetail)

    def tasks(self, limit: int = 50, offset: int = 0, **filters: Any) -> b.Page[b.Task]:
        return self.get("/tasks", b.Page[b.Task], limit=limit, offset=offset, **filters)

    def patch_task(self, task_id: int, patch: b.TaskPatch) -> b.Task:
        body = patch.model_dump(mode="json", exclude_unset=True)
        return self._request("PATCH", f"/tasks/{task_id}", b.Task, json=body)

    def promises(self, limit: int = 50, offset: int = 0, **filters: Any) -> b.Page[b.Promise]:
        return self.get("/promises", b.Page[b.Promise], limit=limit, offset=offset, **filters)

    def audit(self, call_id: str, limit: int = 200, offset: int = 0) -> b.Page[b.AuditRow]:
        return self.get("/audit", b.Page[b.AuditRow], call_id=call_id, limit=limit, offset=offset)

    def outbox(self, limit: int = 50, offset: int = 0, **filters: Any) -> b.Page[b.OutboxMessage]:
        return self.get("/outbox", b.Page[b.OutboxMessage], limit=limit, offset=offset, **filters)


class GatewayServiceClient(_Http):
    """The gateway's read-only knowledge-base browse API (T-GW-12)."""

    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(base_url, {"Authorization": f"Bearer {token}"}, transport)

    def kb_documents(self) -> list[KbDocumentSummary]:
        return self.get("/service/kb/documents", list[KbDocumentSummary])

    def kb_document(self, doc_id: str, version: str | None = None) -> KbDocumentDetail:
        return self.get(f"/service/kb/documents/{doc_id}", KbDocumentDetail, version=version)

    def kb_search(self, query: PolicyQuery) -> PolicySearchResult:
        return self._request(
            "POST",
            "/service/kb/search",
            PolicySearchResult,
            json=query.model_dump(mode="json", exclude_none=True),
        )


class SchedulerClient:
    """Placeholder for the scheduler's trigger API (T-SCHED-06, M3); T-CON-02 fills it in."""

    available = False

    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url

    def _unavailable(self) -> ConsoleApiError:
        return ConsoleApiError("The scheduler arrives in milestone M3 (T-SCHED-06).")

    def run_campaign(self, campaign: str, dry_run: bool = True) -> Any:
        raise self._unavailable()

    def place_call(self, loan_id: int, campaign: str) -> Any:
        raise self._unavailable()

    def active_calls(self) -> Any:
        raise self._unavailable()


@dataclass(frozen=True)
class Clients:
    bank: BankOpsClient
    gateway: GatewayServiceClient
    scheduler: SchedulerClient


def gateway_base_url(gateway_url: str) -> str:
    """`GATEWAY_URL` points at the MCP endpoint (`…/mcp`); the service API lives at its root."""
    return gateway_url.rstrip("/").removesuffix("/mcp")


def build_clients(transport: httpx.BaseTransport | None = None) -> Clients:
    from supportagent.common.settings import get_settings

    s = get_settings().services
    token = s.gateway_service_token.get_secret_value()
    return Clients(
        bank=BankOpsClient(s.bank_api_url, token, transport),
        gateway=GatewayServiceClient(gateway_base_url(s.gateway_url), token, transport),
        scheduler=SchedulerClient(s.scheduler_url, s.scheduler_token.get_secret_value(), transport),
    )


_override: Clients | None = None


def set_clients(clients: Clients | None) -> None:
    """Tests inject fake-backed clients; `None` restores the real ones."""
    global _override
    _override = clients


def get_clients() -> Clients:
    """The clients the console API uses: injected ones in tests, else built from settings once per process."""
    global _cached
    if _override is not None:
        return _override
    if _cached is None:
        _cached = build_clients()
    return _cached


_cached: Clients | None = None
