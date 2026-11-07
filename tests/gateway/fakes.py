"""Test doubles for the gateway: `FakeBank` (a `BankClient` over the 12 persona records), `FakeRetriever`.

`FakeBank` is built from the same persona seed the real bank loads (`bank.seed.personas`, as of
2026-11-09), and computes `LoanStatus` with the bank's own ledger, so values match the real API.
Every call is recorded in `calls` (method name, args) so tests can assert what was — and was not —
written.
"""

import datetime as dt
import itertools
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from supportagent.bank import ledger
from supportagent.bank.seed import PERSONA_BY_ID, PRODUCTS, generate_personas
from supportagent.contracts import bank as c
from supportagent.contracts.kb import PolicyChunk, PolicyQuery, PolicySearchResult
from supportagent.gateway.bank_client import BankError

AS_OF = dt.date(2026, 11, 9)
NOW = dt.datetime(2026, 11, 9, 14, 0, tzinfo=dt.UTC)

FRANK = PERSONA_BY_ID[9002]
HANA = PERSONA_BY_ID[9003]
PRIYA = PERSONA_BY_ID[9005]
OLIVIA = PERSONA_BY_ID[9001]
SAM = PERSONA_BY_ID[9007]
MARCUS = PERSONA_BY_ID[9006]


@dataclass
class FakeBank:
    """In-memory `BankClient`. Set `fail_audit=True` to make `POST /audit` fail (fail-closed tests)."""

    as_of: dt.date = AS_OF
    fail_audit: bool = False
    calls: list[tuple[str, Any]] = field(default_factory=list)
    audit: list[c.AuditIn] = field(default_factory=list)
    promises: list[c.Promise] = field(default_factory=list)
    tasks: list[c.Task] = field(default_factory=list)
    interactions: dict[str, c.Interaction] = field(default_factory=dict)

    def __post_init__(self) -> None:
        data = generate_personas(self.as_of)
        self.customers = {r["id"]: c.Customer.model_validate(r) for r in data.customers}
        self.accounts = {r["id"]: c.Account.model_validate(r) for r in data.accounts}
        self.loans = {r["id"]: c.Loan.model_validate(r) for r in data.loans}
        self.prefs = {
            r["customer_id"]: c.ContactPreferences.model_validate(r) for r in data.contact_preferences
        }
        self.installments: dict[int, list[SimpleNamespace]] = {}
        for r in data.installments:
            self.installments.setdefault(int(r["loan_id"]), []).append(SimpleNamespace(**r))  # type: ignore[call-overload]
        self.txs = [c.Transaction.model_validate(r) for r in data.transactions]
        for r in data.interactions:
            i = c.Interaction.model_validate(r)
            self.interactions[i.call_id] = i
        ids = itertools.count(1)
        self.promises = [c.Promise(id=next(ids), **r) for r in data.promises_to_pay]  # type: ignore[arg-type]
        self._ids = ids

    # ------------------------------------------------------------------ helpers for tests

    def writes(self) -> list[str]:
        return [name for name, _ in self.calls if name in WRITE_METHODS]

    def status_of(self, loan_id: int) -> c.LoanStatus:
        status = _status(self, loan_id)
        assert status is not None
        return status

    # ------------------------------------------------------------------ reads

    async def customers_by_phone(self, phone: str) -> list[c.Customer]:
        self.calls.append(("customers_by_phone", phone))
        return [cu for cu in self.customers.values() if cu.phone == phone]

    async def match_customers(self, dob: dt.date, loan_last4: str) -> list[c.CustomerMatch]:
        self.calls.append(("match_customers", (dob, loan_last4)))
        out = []
        for ln in sorted(self.loans.values(), key=lambda ln: ln.id):
            cu = self.customers.get(ln.customer_id)
            if ln.number[-4:] == loan_last4 and cu is not None and cu.dob == dob:
                out.append(c.CustomerMatch(customer_id=ln.customer_id, loan_id=ln.id))
        return out

    async def get_customer(self, customer_id: int) -> c.Customer | None:
        self.calls.append(("get_customer", customer_id))
        return self.customers.get(customer_id)

    async def customer_loans(self, customer_id: int) -> list[c.Loan]:
        self.calls.append(("customer_loans", customer_id))
        return [ln for ln in self.loans.values() if ln.customer_id == customer_id]

    async def loan_status(self, loan_id: int) -> c.LoanStatus | None:
        self.calls.append(("loan_status", loan_id))
        return _status(self, loan_id)

    async def transactions(self, account_id: int, *, loan_id: int | None, limit: int) -> list[c.Transaction]:
        self.calls.append(("transactions", (account_id, loan_id, limit)))
        rows = [
            t for t in self.txs if t.account_id == account_id and (loan_id is None or t.loan_id == loan_id)
        ]
        return sorted(rows, key=lambda t: (t.ts, t.id), reverse=True)[:limit]

    async def interaction_history(self, customer_id: int, *, limit: int) -> c.InteractionHistory:
        self.calls.append(("interaction_history", customer_id))
        loans = {ln.id for ln in self.loans.values() if ln.customer_id == customer_id}
        calls = sorted(
            (i for i in self.interactions.values() if i.customer_id == customer_id),
            key=lambda i: i.started_at,
            reverse=True,
        )[:limit]
        return c.InteractionHistory(
            interactions=calls,
            open_promises=[p for p in self.promises if p.loan_id in loans and p.status == "open"],
            open_tasks=[t for t in self.tasks if t.customer_id == customer_id and t.status == "open"],
        )

    # ------------------------------------------------------------------ writes

    async def create_promise(self, body: c.PromiseIn) -> c.Promise | None:
        self.calls.append(("create_promise", body))
        if body.loan_id not in self.loans:
            return None
        promise = c.Promise(
            id=next(self._ids),
            call_id=body.call_id,
            loan_id=body.loan_id,
            promised_date=body.promised_date,
            amount=body.amount,
            status="open",
            created_at=NOW,
        )
        self.promises.append(promise)
        return promise

    async def create_task(self, body: c.TaskIn) -> c.Task | None:
        self.calls.append(("create_task", body))
        if body.customer_id not in self.customers:
            return None
        task = c.Task(id=next(self._ids), status="open", created_at=NOW, **body.model_dump())
        self.tasks.append(task)
        return task

    async def patch_contact_preferences(
        self, customer_id: int, body: c.ContactPreferencesPatch
    ) -> c.ContactPreferences | None:
        self.calls.append(("patch_contact_preferences", (customer_id, body)))
        current = self.prefs.get(customer_id)
        if current is None:
            return None
        updated = current.model_copy(update={**body.model_dump(exclude_unset=True), "updated_at": NOW})
        self.prefs[customer_id] = updated
        return updated

    async def upsert_interaction(self, body: c.InteractionIn) -> c.Interaction:
        self.calls.append(("upsert_interaction", body))
        row = c.Interaction(**body.model_dump(), versions=body.outcome.versions if body.outcome else None)
        self.interactions[row.call_id] = row
        return row

    async def append_audit(self, body: c.AuditIn) -> None:
        self.calls.append(("append_audit", body))
        if self.fail_audit:
            raise BankError("POST /audit → 500", 500)
        self.audit.append(body)


WRITE_METHODS = {"create_promise", "create_task", "patch_contact_preferences", "upsert_interaction"}


def _status(bank: FakeBank, loan_id: int) -> c.LoanStatus | None:
    loan = bank.loans.get(loan_id)
    if loan is None:
        return None
    figures = ledger.summary(sorted(bank.installments[loan_id], key=lambda i: i.seq), bank.as_of)
    return c.LoanStatus.model_validate(
        {
            "loan_id": loan.id,
            "customer_id": loan.customer_id,
            "number_last4": loan.number[-4:],
            "product_code": loan.product_code,
            "product_name": PRODUCTS[loan.product_code].name,
            "as_of": bank.as_of,
            "status": loan.status,
            **figures,
        }
    )


def seeded_numbers(bank: FakeBank) -> list[str]:
    """Every loan/account number and phone in the fake bank (for `assert_no_pii`)."""
    return (
        [ln.number for ln in bank.loans.values()]
        + [a.number for a in bank.accounts.values()]
        + [cu.phone for cu in bank.customers.values()]
    )


@dataclass
class FakeRetriever:
    """A `Retriever` returning fixed chunks; `confident` decides `low_confidence`."""

    chunks: list[PolicyChunk] = field(default_factory=list)
    confident: bool = True
    queries: list[PolicyQuery] = field(default_factory=list)

    async def search(self, query: PolicyQuery) -> PolicySearchResult:
        self.queries.append(query)
        best = max((ch.similarity or 0.0 for ch in self.chunks), default=0.0)
        return PolicySearchResult(
            chunks=self.chunks[: query.top_k], confident=self.confident, best_similarity=best
        )


def chunk(
    doc_id: str = "late-fees-penalty-interest", text: str = "A late fee applies.", **kw: Any
) -> PolicyChunk:
    values: dict[str, Any] = {
        "doc_id": doc_id,
        "version": "1.0",
        "title": "Late fees",
        "section": "Late fees > Personal loan",
        "product": "all",
        "text": text,
        "rrf_score": 0.03,
        "similarity": 0.8,
    }
    values.update(kw)
    return PolicyChunk(**values)


__all__ = [
    "AS_OF",
    "FRANK",
    "HANA",
    "NOW",
    "OLIVIA",
    "PRIYA",
    "SAM",
    "Decimal",
    "FakeBank",
    "FakeRetriever",
    "chunk",
]
