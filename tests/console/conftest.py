"""Fakes for the console: the bank API and the gateway browse API served through `httpx.MockTransport`.

The bank data comes from the gateway tests' `FakeBank` (the real persona seed as of 2026-11-09 with
the bank's own ledger), so figures equal what the real API returns. The console API tests build their
app with `make_clients(FakeApi())`; `fake_api` also points `clients.get_clients()` at the fakes — no
network. The same fakes record the Next.js e2e fixtures (`tests/console/web_fixtures.py`).
"""

import datetime as dt
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from supportagent.bank.seed import PERSONAS
from supportagent.console import clients as cl
from supportagent.contracts import bank as c
from supportagent.contracts.kb import (
    KbChunkView,
    KbDocumentDetail,
    KbDocumentSummary,
    KbDocumentVersion,
    PolicyChunk,
    PolicyQuery,
    PolicySearchResult,
)
from tests.gateway.fakes import FakeBank, _status

BANK_URL = "http://bank.test"
GATEWAY_URL = "http://gateway.test"
TOKEN = "test-service-token"
CALL_ID = "fbde0dde-804f-4a79-ba80-2795d29b3079"
TS = dt.datetime(2026, 11, 9, 14, 0, tzinfo=dt.UTC)

FEES_TEXT = "## Late fees\n\n| Product | Late fee |\n|---|---|\n| Personal Loan | $25 flat |"


def _json(model: Any) -> Any:
    if isinstance(model, BaseModel):
        return model.model_dump(mode="json")
    if isinstance(model, list):
        return [_json(m) for m in model]
    return model


@dataclass
class FakeApi:
    """Both upstream APIs. `fail` maps a path prefix to an HTTP status to simulate errors."""

    bank: FakeBank = field(default_factory=FakeBank)
    fail: dict[str, int] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    audit: list[c.AuditRow] = field(default_factory=list)

    def __post_init__(self) -> None:
        tools = [
            ("verify_identity", "allow"),
            ("get_loan_status", "allow"),
            ("search_policy", "allow"),
            ("record_promise_to_pay", "deny"),
            ("record_call_outcome", "allow"),
        ]
        self.audit = [
            c.AuditRow(
                id=i + 1,
                ts=TS + dt.timedelta(seconds=i),
                call_id=CALL_ID,
                tool=tool,
                args_hash="0" * 64,
                customer_id=9002 if i else None,
                decision=decision,  # type: ignore[arg-type]
                latency_ms=5 + i,
            )
            for i, (tool, decision) in enumerate(tools)
        ]

    # ------------------------------------------------------------------ routing

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        for prefix, status in self.fail.items():
            if path.startswith(prefix):
                return httpx.Response(status, json={"detail": "simulated failure"})
        host = request.url.host
        if host == "gateway.test":
            if request.headers.get("authorization") != f"Bearer {TOKEN}":
                return httpx.Response(401)
            return self._gateway(request)
        if request.headers.get("x-service-token") != TOKEN:
            return httpx.Response(401)
        return self._bank(request)

    def _bank(self, request: httpx.Request) -> httpx.Response:
        b, path, q = self.bank, request.url.path, request.url.params
        limit, offset = int(q.get("limit", 50)), int(q.get("offset", 0))

        def page(items: list[Any]) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": _json(items[offset : offset + limit]),
                    "total": len(items),
                    "limit": limit,
                    "offset": offset,
                },
            )

        if path == "/customers/search":
            term = q.get("q", "").lower()
            rows = []
            for cu in sorted(b.customers.values(), key=lambda x: x.id):
                loans = [ln for ln in b.loans.values() if ln.customer_id == cu.id]
                hay = f"{cu.full_name.lower()} {cu.phone} {cu.id} " + " ".join(ln.number[-4:] for ln in loans)
                if term and term not in hay:
                    continue
                buckets = [_status(b, ln.id).bucket for ln in loans]  # type: ignore[union-attr]
                rows.append(
                    c.CustomerSummary(
                        customer_id=cu.id,
                        full_name=cu.full_name,
                        phone=cu.phone,
                        segment=cu.segment,
                        loan_count=len(loans),
                        worst_bucket=max(buckets, key=_bucket_rank) if buckets else None,
                    )
                )
            return page(rows)
        if path == "/loans":
            statuses = [_status(b, ln.id) for ln in sorted(b.loans.values(), key=lambda x: x.id)]
            if q.get("bucket"):
                statuses = [s for s in statuses if s.bucket == q["bucket"]]  # type: ignore[union-attr]
            if q.get("product"):
                statuses = [s for s in statuses if s.product_code == q["product"]]  # type: ignore[union-attr]
            return page(statuses)
        if path == "/personas":
            return httpx.Response(200, json=[_persona(p) for p in PERSONAS])
        if m := re.fullmatch(r"/customers/(\d+)", path):
            cu = b.customers.get(int(m[1]))
            return httpx.Response(200, json=_json(cu)) if cu else httpx.Response(404, json={"detail": "nope"})
        if m := re.fullmatch(r"/customers/(\d+)/loans", path):
            return httpx.Response(
                200, json=_json([ln for ln in b.loans.values() if ln.customer_id == int(m[1])])
            )
        if m := re.fullmatch(r"/customers/(\d+)/contact-preferences", path):
            return httpx.Response(200, json=_json(b.prefs[int(m[1])]))
        if m := re.fullmatch(r"/customers/(\d+)/interactions", path):
            cid = int(m[1])
            loans = {ln.id for ln in b.loans.values() if ln.customer_id == cid}
            history = c.InteractionHistory(
                interactions=[i for i in b.interactions.values() if i.customer_id == cid],
                open_promises=[p for p in b.promises if p.loan_id in loans and p.status == "open"],
                open_tasks=[],
            )
            return httpx.Response(200, json=_json(history))
        if m := re.fullmatch(r"/loans/(\d+)", path):
            return httpx.Response(200, json=_json(_status(b, int(m[1]))))
        if m := re.fullmatch(r"/loans/(\d+)/installments", path):
            rows = sorted(b.installments[int(m[1])], key=lambda i: i.seq)
            return httpx.Response(200, json=[_installment(i) for i in rows])
        if m := re.fullmatch(r"/accounts/(\d+)/transactions", path):
            tx = [t for t in b.txs if t.account_id == int(m[1])]
            tx = sorted(tx, key=lambda t: (t.ts, t.id), reverse=True)[: int(q.get("limit", 20))]
            return httpx.Response(200, json=_json(tx))
        if path == "/audit":
            return page([a for a in self.audit if a.call_id == q.get("call_id")])
        if path == "/interactions":
            return page(sorted(b.interactions.values(), key=lambda i: i.started_at, reverse=True))
        if path == "/tasks":
            return page([])
        return httpx.Response(404, json={"detail": f"no fake for {path}"})

    def _gateway(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/service/kb/documents":
            return httpx.Response(200, json=_json(KB_DOCS))
        if m := re.fullmatch(r"/service/kb/documents/([\w-]+)", path):
            detail = KB_DETAIL.get(m[1])
            return (
                httpx.Response(200, json=_json(detail))
                if detail
                else httpx.Response(404, json={"detail": "x"})
            )
        if path == "/service/kb/search":
            query = PolicyQuery.model_validate(json.loads(request.content))
            return httpx.Response(200, json=_json(_search(query)))
        return httpx.Response(404)


def _bucket_rank(bucket: str) -> int:
    return ["current", "1-30", "31-60", "61-90", "90+"].index(bucket)


def _persona(p: Any) -> Any:
    return c.PersonaCard(
        customer_id=p.customer_id,
        full_name=p.name,
        phone=p.phone,
        extension=p.extension,
        dob=p.dob,
        loan_id=p.loan_id,
        loan_last4=p.loan_last4,
        product_code=p.product_code,
        situation=p.situation,
        expected_outcome=p.expected_outcome,
    ).model_dump(mode="json")


def _installment(i: Any) -> Any:
    return c.Installment.model_validate({k: getattr(i, k) for k in c.Installment.model_fields}).model_dump(
        mode="json"
    )


KB_DOCS = [
    KbDocumentSummary(
        doc_id="fees-schedule",
        title="Schedule of Loan Fees",
        product="all",
        audience="customer",
        versions=[KbDocumentVersion(version="1.0", effective_date=dt.date(2026, 1, 1), chunk_count=1)],
    ),
    KbDocumentSummary(
        doc_id="late-fees-penalty-interest",
        title="Grace Periods, Late Fees and Penalty Interest",
        product="all",
        audience="customer",
        versions=[
            KbDocumentVersion(version="1.1", effective_date=dt.date(2027, 1, 1), chunk_count=2),
            KbDocumentVersion(version="1.0", effective_date=dt.date(2026, 1, 1), chunk_count=2),
        ],
    ),
    KbDocumentSummary(
        doc_id="contact-calling-rules",
        title="Contact and Calling Rules",
        product="all",
        audience="internal",
        versions=[KbDocumentVersion(version="1.0", effective_date=dt.date(2026, 1, 1), chunk_count=1)],
    ),
]
KB_DETAIL = {
    "late-fees-penalty-interest": KbDocumentDetail(
        doc_id="late-fees-penalty-interest",
        version="1.0",
        title="Grace Periods, Late Fees and Penalty Interest",
        product="all",
        audience="customer",
        effective_date=dt.date(2026, 1, 1),
        chunks=[
            KbChunkView(
                ord=0,
                section="Grace Periods > Grace periods by product",
                text="Personal: 10 days.",
                token_count=5,
            ),
            KbChunkView(ord=1, section="Grace Periods > Late fees", text=FEES_TEXT, token_count=20),
        ],
    ),
    "fees-schedule": KbDocumentDetail(
        doc_id="fees-schedule",
        version="1.0",
        title="Schedule of Loan Fees",
        product="all",
        audience="customer",
        effective_date=dt.date(2026, 1, 1),
        chunks=[KbChunkView(ord=0, section="Schedule of Loan Fees", text=FEES_TEXT, token_count=20)],
    ),
    "contact-calling-rules": KbDocumentDetail(
        doc_id="contact-calling-rules",
        version="1.0",
        title="Contact and Calling Rules",
        product="all",
        audience="internal",
        effective_date=dt.date(2026, 1, 1),
        chunks=[
            KbChunkView(ord=0, section="Calling hours", text="08:00 to 21:00 local time.", token_count=6)
        ],
    ),
}


def _search(q: PolicyQuery) -> PolicySearchResult:
    if "late fee" not in q.query.lower():
        return PolicySearchResult(chunks=[], confident=False, best_similarity=0.05)
    chunk = PolicyChunk(
        doc_id="fees-schedule",
        version="1.0",
        title="Schedule of Loan Fees",
        section="Schedule of Loan Fees",
        product="all",
        text=FEES_TEXT,
        rrf_score=0.0328,
        similarity=0.71,
    )
    return PolicySearchResult(chunks=[chunk], confident=True, best_similarity=0.71)


def make_clients(api: FakeApi) -> cl.Clients:
    transport = httpx.MockTransport(api.handler)
    return cl.Clients(
        bank=cl.BankOpsClient(BANK_URL, TOKEN, transport),
        gateway=cl.GatewayServiceClient(GATEWAY_URL, TOKEN, transport),
        scheduler=cl.SchedulerClient("http://scheduler.test", TOKEN, transport),
    )


@pytest.fixture
def fake_api() -> Iterator[FakeApi]:
    api = FakeApi()
    cl.set_clients(make_clients(api))
    yield api
    cl.set_clients(None)
