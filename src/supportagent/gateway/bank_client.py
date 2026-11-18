"""Access to the mock core bank's internal API (specs/02-mock-bank is authoritative).

`BankClient` is the protocol the gateway codes against; `HttpBankClient` is the real implementation
(httpx, 2 s timeouts, one retry on GET only — writes are never retried, so a timeout cannot double a
promise or a task). Unit tests use `tests/gateway/fakes.FakeBank`.

Lookups that the bank answers with 404 return `None` (or an empty list); every other failure raises
`BankError`, which the tool pipeline turns into `upstream_error`.
"""

import datetime as dt
from typing import Any, Protocol

import httpx
from pydantic import TypeAdapter

from supportagent.contracts import bank as c

TIMEOUT_S = 2.0


class BankError(RuntimeError):
    """The bank API failed (network error, 5xx, unexpected 4xx)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class BankClient(Protocol):
    async def customers_by_phone(self, phone: str) -> list[c.Customer]: ...
    async def match_customers(self, dob: dt.date, loan_last4: str) -> list[c.CustomerMatch]: ...
    async def get_customer(self, customer_id: int) -> c.Customer | None: ...
    async def customer_loans(self, customer_id: int) -> list[c.Loan]: ...
    async def loan_status(self, loan_id: int) -> c.LoanStatus | None: ...
    async def transactions(
        self, account_id: int, *, loan_id: int | None, limit: int
    ) -> list[c.Transaction]: ...
    async def interaction_history(self, customer_id: int, *, limit: int) -> c.InteractionHistory: ...
    async def create_promise(self, body: c.PromiseIn) -> c.Promise | None: ...
    async def create_task(self, body: c.TaskIn) -> c.Task | None: ...
    async def patch_contact_preferences(
        self, customer_id: int, body: c.ContactPreferencesPatch
    ) -> c.ContactPreferences | None: ...
    async def upsert_interaction(self, body: c.InteractionIn) -> c.Interaction: ...
    async def append_audit(self, body: c.AuditIn) -> None: ...


_customers = TypeAdapter(list[c.Customer])
_matches = TypeAdapter(list[c.CustomerMatch])
_loans = TypeAdapter(list[c.Loan])
_transactions = TypeAdapter(list[c.Transaction])


class HttpBankClient:
    """`BankClient` over HTTP. Pass `transport` (e.g. `httpx.MockTransport` or an ASGI transport) in tests."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = TIMEOUT_S,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-Service-Token": service_token},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ plumbing

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response | None:
        """GET with one retry on transport errors and 5xx. Returns None on 404."""
        last: Exception | None = None
        for _ in range(2):
            try:
                resp = await self._client.get(path, params=params)
            except httpx.TransportError as exc:
                last = exc
                continue
            if resp.status_code >= 500:
                last = BankError(f"GET {path} → {resp.status_code}", resp.status_code)
                continue
            if resp.status_code == 404:
                return None
            if resp.is_error:
                raise BankError(f"GET {path} → {resp.status_code}", resp.status_code)
            return resp
        raise BankError(f"GET {path} failed: {last}") from last

    async def _send(self, method: str, path: str, body: Any) -> httpx.Response | None:
        """POST/PATCH without retry. Returns None on 404."""
        try:
            # exclude_unset keeps PATCH semantics (only the fields that were set are sent)
            resp = await self._client.request(
                method,
                path,
                content=body.model_dump_json(exclude_unset=True),
                headers={"Content-Type": "application/json"},
            )
        except httpx.TransportError as exc:
            raise BankError(f"{method} {path} failed: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.is_error:
            raise BankError(f"{method} {path} → {resp.status_code}", resp.status_code)
        return resp

    # ------------------------------------------------------------------ reads

    async def customers_by_phone(self, phone: str) -> list[c.Customer]:
        resp = await self._get("/customers", {"phone": phone})
        return [] if resp is None else _customers.validate_json(resp.content)

    async def match_customers(self, dob: dt.date, loan_last4: str) -> list[c.CustomerMatch]:
        """Loans whose customer has this DOB and whose number ends in `loan_last4` (ids only, T-BANK-08)."""
        resp = await self._get("/customers/match", {"dob": dob.isoformat(), "loan_last4": loan_last4})
        return [] if resp is None else _matches.validate_json(resp.content)

    async def get_customer(self, customer_id: int) -> c.Customer | None:
        resp = await self._get(f"/customers/{customer_id}")
        return None if resp is None else c.Customer.model_validate_json(resp.content)

    async def customer_loans(self, customer_id: int) -> list[c.Loan]:
        resp = await self._get(f"/customers/{customer_id}/loans")
        return [] if resp is None else _loans.validate_json(resp.content)

    async def loan_status(self, loan_id: int) -> c.LoanStatus | None:
        resp = await self._get(f"/loans/{loan_id}")
        return None if resp is None else c.LoanStatus.model_validate_json(resp.content)

    async def transactions(self, account_id: int, *, loan_id: int | None, limit: int) -> list[c.Transaction]:
        params: dict[str, Any] = {"limit": limit}
        if loan_id is not None:
            params["loan_id"] = loan_id
        resp = await self._get(f"/accounts/{account_id}/transactions", params)
        return [] if resp is None else _transactions.validate_json(resp.content)

    async def interaction_history(self, customer_id: int, *, limit: int) -> c.InteractionHistory:
        resp = await self._get(f"/customers/{customer_id}/interactions", {"limit": limit})
        if resp is None:
            return c.InteractionHistory(interactions=[], open_promises=[], open_tasks=[])
        return c.InteractionHistory.model_validate_json(resp.content)

    # ------------------------------------------------------------------ writes (interaction tables only)

    async def create_promise(self, body: c.PromiseIn) -> c.Promise | None:
        resp = await self._send("POST", "/promises", body)
        return None if resp is None else c.Promise.model_validate_json(resp.content)

    async def create_task(self, body: c.TaskIn) -> c.Task | None:
        resp = await self._send("POST", "/tasks", body)
        return None if resp is None else c.Task.model_validate_json(resp.content)

    async def patch_contact_preferences(
        self, customer_id: int, body: c.ContactPreferencesPatch
    ) -> c.ContactPreferences | None:
        resp = await self._send("PATCH", f"/contact-preferences/{customer_id}", body)
        return None if resp is None else c.ContactPreferences.model_validate_json(resp.content)

    async def upsert_interaction(self, body: c.InteractionIn) -> c.Interaction:
        resp = await self._send("POST", "/interactions", body)
        if resp is None:
            raise BankError("POST /interactions → 404", 404)
        return c.Interaction.model_validate_json(resp.content)

    async def append_audit(self, body: c.AuditIn) -> None:
        resp = await self._send("POST", "/audit", body)
        if resp is None:
            raise BankError("POST /audit → 404", 404)
