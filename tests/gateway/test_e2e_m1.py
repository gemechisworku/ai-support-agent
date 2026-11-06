"""T-GW-09 (integration): the M1 exit demo end to end.

Real bank API (in-process ASGI, seeded `bank_test`) → real gateway (uvicorn on a local port, MCP over
streamable HTTP) → real KB (corpus ingested into `kb_test`, fake embedder). Runs `scripts/demo_m1.py`'s
flow for Forgetful Frank and checks the results and the audit trail.
"""

import datetime as dt
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy import text
from sqlalchemy.orm import Session

import demo_m1  # scripts/ is on the pytest pythonpath
from supportagent.bank.api import create_app as create_bank_app
from supportagent.bank.api.deps import get_session
from supportagent.common.settings import Settings, get_settings
from supportagent.gateway.app import create_app
from supportagent.gateway.bank_client import HttpBankClient
from supportagent.gateway.config import GatewayConfig
from supportagent.gateway.retriever import KbRetriever
from supportagent.gateway.tools import Gateway
from supportagent.kb.embed import HashEmbedder
from supportagent.kb.ingest import ingest
from supportagent.kb.retrieval import HASH_MIN_SIMILARITY
from tests.bank.conftest import seeded_bank  # noqa: F401 (fixture)
from tests.conftest import TEST_ENV

pytestmark = pytest.mark.integration
CORPUS = Path(__file__).resolve().parents[2] / "data" / "policies"
AS_OF = dt.date(2026, 11, 9)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def stack(
    seeded_bank: Settings,  # noqa: F811
    bank_db: Session,
    kb_db: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    monkeypatch.setenv("BANK_AS_OF", AS_OF.isoformat())  # the gateway thread does not see a frozen clock
    get_settings.cache_clear()
    bank_app = create_bank_app()

    def session_override() -> Iterator[Session]:
        yield bank_db
        bank_db.flush()

    bank_app.dependency_overrides[get_session] = session_override
    embedder = HashEmbedder()
    ingest(CORPUS, kb_db, embedder)
    config = GatewayConfig(
        worker_token=TEST_ENV["GATEWAY_WORKER_TOKEN"],
        service_token=TEST_ENV["GATEWAY_SERVICE_TOKEN"],
        audit_salt=TEST_ENV["AUDIT_HASH_SALT"],
    )
    bank = HttpBankClient(
        "http://bank", config.service_token, transport=httpx.ASGITransport(app=bank_app), timeout=30
    )
    gateway = Gateway(
        bank=bank,
        config=config,
        retriever=KbRetriever(lambda: nullcontext(kb_db), embedder, HASH_MIN_SIMILARITY),
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(gateway), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(200):
        try:
            if httpx.get(f"{base}/healthz", timeout=0.5).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.05)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


async def test_m1_exit_demo(stack: str, bank_db: Session) -> None:
    demo = await demo_m1.run_demo(stack, TEST_ENV["GATEWAY_WORKER_TOKEN"])
    assert [tool for tool, _ in demo.steps] == [
        "verify_identity", "get_loan_status", "search_policy", "record_promise_to_pay", "record_call_outcome",
    ]  # fmt: skip
    assert all(payload["ok"] for _, payload in demo.steps), demo.steps

    assert demo.data("verify_identity")["verified"] is True
    loan = demo.data("get_loan_status")["loans"][0]
    assert loan["days_past_due"] == 5 and loan["loan_masked"] == "****4417"
    policy = demo.data("search_policy")
    assert "late-fees-penalty-interest" in {c["doc_id"] for c in policy["chunks"]}
    assert demo.data("record_promise_to_pay")["accepted"] is True

    audit = bank_db.execute(
        text("select tool, decision, customer_id from audit_log where call_id = :c order by ts"),
        {"c": demo.call_id},
    ).all()
    assert [r.tool for r in audit] == [tool for tool, _ in demo.steps]  # audit rows == tool calls
    assert {r.decision for r in audit} == {"allow"}
    assert audit[0].customer_id == 9002  # bound by verify_identity

    promise = bank_db.execute(
        text("select amount, promised_date from promises_to_pay where call_id = :c"), {"c": demo.call_id}
    ).one()
    assert promise.amount == Decimal(loan["overdue_amount"])
    assert promise.promised_date == AS_OF + dt.timedelta(days=3)
    row = bank_db.execute(
        text("select contact_result, outcome from interactions where call_id = :c"), {"c": demo.call_id}
    ).one()
    assert row.contact_result == "right_party"
    assert any(src.startswith("late-fees-penalty-interest@") for src in row.outcome["kb_sources"])
