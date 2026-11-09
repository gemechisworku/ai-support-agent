"""T-GW-12 (integration): the KB browse API over an ingested `kb_test` (HashEmbedder).

The search route must return exactly what `search_policy` retrieves for the same query.
"""

from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from supportagent.common.clock import FrozenClock
from supportagent.contracts.kb import KbDocumentDetail, KbDocumentSummary, PolicySearchResult
from supportagent.gateway.app import create_app
from supportagent.gateway.kb_browse import SqlKbBrowser
from supportagent.gateway.retriever import KbRetriever
from supportagent.gateway.tools import Gateway, run_tool
from supportagent.kb.embed import HashEmbedder
from supportagent.kb.ingest import ingest
from supportagent.kb.retrieval import HASH_MIN_SIMILARITY
from tests.gateway.conftest import CONFIG, open_session

CORPUS = Path(__file__).resolve().parents[2] / "data" / "policies"
SERVICE = {"Authorization": f"Bearer {CONFIG.service_token}"}
pytestmark = pytest.mark.integration


@pytest.fixture
def kb_gateway(gateway: Gateway, kb_db: Session) -> Gateway:
    embedder = HashEmbedder()
    ingest(CORPUS, kb_db, embedder)
    gateway.retriever = KbRetriever(lambda: nullcontext(kb_db), embedder, HASH_MIN_SIMILARITY)
    gateway.kb_browser = SqlKbBrowser(lambda: nullcontext(kb_db))
    return gateway


@pytest.fixture
def client(kb_gateway: Gateway) -> TestClient:
    return TestClient(create_app(kb_gateway))


def test_lists_the_whole_corpus(client: TestClient) -> None:
    r = client.get("/service/kb/documents", headers=SERVICE)
    assert r.status_code == 200
    docs = [KbDocumentSummary.model_validate(d) for d in r.json()]
    assert len(docs) == len(list(CORPUS.glob("*.md"))) == 20
    assert [d.doc_id for d in docs] == sorted(d.doc_id for d in docs)
    assert all(d.versions and d.versions[0].chunk_count >= 1 for d in docs)


def test_document_chunks_in_order(client: TestClient) -> None:
    r = client.get("/service/kb/documents/fees-schedule?version=1.0", headers=SERVICE)
    assert r.status_code == 200
    doc = KbDocumentDetail.model_validate(r.json())
    assert doc.version == "1.0" and doc.title
    assert [c.ord for c in doc.chunks] == sorted(c.ord for c in doc.chunks)
    assert "Late fee" in " ".join(c.text for c in doc.chunks)
    default = client.get("/service/kb/documents/fees-schedule", headers=SERVICE)
    assert KbDocumentDetail.model_validate(default.json()) == doc  # one version: the default is it


async def test_search_equals_search_policy(
    kb_gateway: Gateway, client: TestClient, frozen_clock: FrozenClock
) -> None:
    query = {"query": "late fee personal loan", "product": "PERSONAL", "top_k": 4}
    r = client.post("/service/kb/search", headers=SERVICE, json=query)
    assert r.status_code == 200
    browsed = PolicySearchResult.model_validate(r.json())
    assert browsed.confident

    session = await open_session(kb_gateway)
    tool = await run_tool(
        kb_gateway, "search_policy", session.token, {"query": query["query"], "product": "PERSONAL"}
    )
    assert tool.data is not None
    assert [c.citation for c in browsed.chunks] == [c.citation for c in tool.data.chunks]
    assert not tool.data.low_confidence
