"""T-GW-12 · GW-R12 (unit): the console's knowledge-base browse API over a fake KB and FakeBank.

Covers the routes, the service-token auth matrix (service ok; worker/session token 403; none 401),
one audit row per request with `call_id="service"`, and fail-closed auditing.
"""

import datetime as dt
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from supportagent.common.clock import FrozenClock
from supportagent.contracts.kb import KbChunkView, KbDocumentDetail, KbDocumentSummary, KbDocumentVersion
from supportagent.gateway.app import create_app
from supportagent.gateway.tools import Gateway
from tests.gateway.conftest import CONFIG, open_session
from tests.gateway.fakes import FakeBank, FakeRetriever, chunk

SERVICE = {"Authorization": f"Bearer {CONFIG.service_token}"}
WORKER = {"Authorization": f"Bearer {CONFIG.worker_token}"}

FEES = KbDocumentDetail(
    doc_id="fees-schedule",
    version="1.0",
    title="Schedule of Loan Fees",
    product="all",
    audience="customer",
    effective_date=dt.date(2026, 1, 1),
    chunks=[KbChunkView(ord=0, section="Schedule of Loan Fees", text="## Late fees\n...", token_count=120)],
)


@dataclass
class FakeKbBrowser:
    docs: dict[tuple[str, str], KbDocumentDetail] = field(
        default_factory=lambda: {("fees-schedule", "1.0"): FEES}
    )

    def list_documents(self) -> list[KbDocumentSummary]:
        return [
            KbDocumentSummary(
                doc_id=d.doc_id,
                title=d.title,
                product=d.product,
                audience=d.audience,
                versions=[
                    KbDocumentVersion(version=d.version, effective_date=d.effective_date, chunk_count=1)
                ],
            )
            for d in self.docs.values()
        ]

    def document(self, doc_id: str, version: str | None = None) -> KbDocumentDetail | None:
        matches = [d for (i, v), d in self.docs.items() if i == doc_id and version in (None, v)]
        return matches[0] if matches else None


@pytest.fixture
def kb_gateway(gateway: Gateway) -> Gateway:
    gateway.kb_browser = FakeKbBrowser()
    gateway.retriever = FakeRetriever(chunks=[chunk("fees-schedule", "Schedule of Loan Fees")])
    return gateway


@pytest.fixture
def client(kb_gateway: Gateway) -> TestClient:
    return TestClient(create_app(kb_gateway))


def test_list_documents(client: TestClient) -> None:
    r = client.get("/service/kb/documents", headers=SERVICE)
    assert r.status_code == 200
    (doc,) = [KbDocumentSummary.model_validate(d) for d in r.json()]
    assert doc.doc_id == "fees-schedule"
    assert doc.versions[0].chunk_count == 1


def test_get_document_default_and_explicit_version(client: TestClient) -> None:
    r = client.get("/service/kb/documents/fees-schedule", headers=SERVICE)
    assert r.status_code == 200
    assert KbDocumentDetail.model_validate(r.json()) == FEES
    assert client.get("/service/kb/documents/fees-schedule?version=1.0", headers=SERVICE).status_code == 200


@pytest.mark.parametrize(
    "path", ["/service/kb/documents/nope", "/service/kb/documents/fees-schedule?version=9.9"]
)
def test_unknown_document_or_version_is_404(client: TestClient, path: str, bank: FakeBank) -> None:
    assert client.get(path, headers=SERVICE).status_code == 404
    assert bank.audit[-1].decision == "deny"


def test_search_returns_the_retriever_result(client: TestClient, kb_gateway: Gateway) -> None:
    r = client.post("/service/kb/search", headers=SERVICE, json={"query": "late fee", "product": "PERSONAL"})
    assert r.status_code == 200
    body = r.json()
    assert body["confident"] is True
    assert [c["doc_id"] for c in body["chunks"]] == ["fees-schedule"]
    assert kb_gateway.retriever.queries[-1].product == "PERSONAL"  # type: ignore[union-attr]


def test_search_validates_the_query(client: TestClient) -> None:
    assert client.post("/service/kb/search", headers=SERVICE, json={"query": ""}).status_code == 422
    assert (
        client.post("/service/kb/search", headers=SERVICE, json={"query": "x", "extra": 1}).status_code == 422
    )


async def test_auth_matrix(kb_gateway: Gateway, client: TestClient, frozen_clock: FrozenClock) -> None:
    session = await open_session(kb_gateway)
    cases = {
        "none": ({}, 401),
        "worker": (WORKER, 403),
        "session": ({"Authorization": f"Bearer {session.token}"}, 403),
        "garbage": ({"Authorization": "Bearer nope"}, 403),
        "service": (SERVICE, 200),
    }
    for name, (headers, expected) in cases.items():
        assert client.get("/service/kb/documents", headers=headers).status_code == expected, name
        r = client.post("/service/kb/search", headers=headers, json={"query": "late fee"})
        assert r.status_code == expected, name


def test_every_request_is_audited_as_service(client: TestClient, bank: FakeBank) -> None:
    client.get("/service/kb/documents", headers=SERVICE)
    client.get("/service/kb/documents/fees-schedule", headers=SERVICE)
    client.post("/service/kb/search", headers=SERVICE, json={"query": "late fee"})
    client.get("/service/kb/documents", headers=WORKER)
    rows = bank.audit[-4:]
    assert [r.tool for r in rows] == [
        "kb.list_documents",
        "kb.get_document",
        "kb.search",
        "kb.list_documents",
    ]
    assert [r.decision for r in rows] == ["allow", "allow", "allow", "deny"]
    assert {r.call_id for r in rows} == {"service"}
    assert all(r.customer_id is None for r in rows)


def test_no_bank_data_is_read(client: TestClient, bank: FakeBank) -> None:
    client.get("/service/kb/documents", headers=SERVICE)
    client.post("/service/kb/search", headers=SERVICE, json={"query": "late fee"})
    assert {name for name, _ in bank.calls} == {"append_audit"}


def test_audit_failure_fails_closed(client: TestClient, bank: FakeBank) -> None:
    bank.fail_audit = True
    r = client.get("/service/kb/documents", headers=SERVICE)
    assert r.status_code == 503
    assert r.json()["detail"] == "audit_unavailable"


def test_unconfigured_browser_is_503(gateway: Gateway) -> None:
    client = TestClient(create_app(gateway))
    assert client.get("/service/kb/documents", headers=SERVICE).status_code == 503


def test_retriever_failure_is_upstream_error(client: TestClient, kb_gateway: Gateway, bank: FakeBank) -> None:
    class Broken:
        async def search(self, query: object) -> object:
            raise RuntimeError("embedder down")

    kb_gateway.retriever = Broken()  # type: ignore[assignment]
    r = client.post("/service/kb/search", headers=SERVICE, json={"query": "late fee"})
    assert r.status_code == 502
    assert bank.audit[-1].decision == "error"


def test_service_rate_limit(client: TestClient, kb_gateway: Gateway) -> None:
    for _ in range(kb_gateway.config.limits.credential_per_minute):
        assert client.get("/service/kb/documents", headers=SERVICE).status_code == 200
    assert client.get("/service/kb/documents", headers=SERVICE).status_code == 429
