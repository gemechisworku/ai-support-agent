"""T-GW-08 (integration): `search_policy` over the real KB (ingested corpus, fake embedder)."""

from contextlib import nullcontext
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from supportagent.gateway.retriever import KbRetriever
from supportagent.gateway.tools import Gateway, run_tool
from supportagent.kb.embed import HashEmbedder
from supportagent.kb.ingest import ingest
from supportagent.kb.retrieval import HASH_MIN_SIMILARITY
from tests.gateway.conftest import open_session
from tests.gateway.fakes import FRANK

CORPUS = Path(__file__).resolve().parents[2] / "data" / "policies"
pytestmark = pytest.mark.integration


@pytest.fixture
def kb_gateway(gateway: Gateway, kb_db: Session) -> Gateway:
    embedder = HashEmbedder()
    ingest(CORPUS, kb_db, embedder)
    gateway.retriever = KbRetriever(lambda: nullcontext(kb_db), embedder, HASH_MIN_SIMILARITY)
    return gateway


async def test_late_fee_question_cites_the_late_fee_policy(kb_gateway: Gateway) -> None:
    s = await open_session(kb_gateway, loan_id=FRANK.loan_id)
    r = await run_tool(
        kb_gateway, "search_policy", s.token, {"query": "late fee personal loan", "product": "PERSONAL"}
    )
    assert r.data is not None and not r.data.low_confidence
    assert 1 <= len(r.data.chunks) <= 4
    assert "late-fees-penalty-interest" in {c.doc_id for c in r.data.chunks}
    assert all(src.startswith(tuple(c.doc_id for c in r.data.chunks)) for src in s.kb_sources)


async def test_off_topic_question_is_low_confidence(kb_gateway: Gateway) -> None:
    s = await open_session(kb_gateway, loan_id=FRANK.loan_id)
    r = await run_tool(
        kb_gateway, "search_policy", s.token, {"query": "who won the football match yesterday"}
    )
    assert r.data is not None and r.data.low_confidence
