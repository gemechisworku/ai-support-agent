"""T-GW-11 (integration): an inbound caller from an unregistered number verifies against the seeded bank.

The gateway talks to the real bank API app (in process, ASGI transport) over `bank_test` seeded with
the synthetic population; `GET /customers/match` supplies the candidates.
"""

import uuid
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy.orm import Session as DbSession

from supportagent.bank.api import create_app as create_bank_app
from supportagent.bank.api.deps import get_session
from supportagent.common.clock import FrozenClock
from supportagent.common.settings import Settings
from supportagent.contracts.gateway import SessionCreate
from supportagent.gateway.bank_client import HttpBankClient
from supportagent.gateway.session import Session
from supportagent.gateway.tools import Gateway, run_tool
from tests.bank.conftest import seeded_bank  # noqa: F401 (fixture)
from tests.conftest import TEST_ENV
from tests.gateway.conftest import CONFIG
from tests.gateway.fakes import FRANK

pytestmark = pytest.mark.integration
UNKNOWN = "+15559990000"


@pytest.fixture
def real_gateway(
    seeded_bank: Settings,  # noqa: F811
    bank_db: DbSession,
    settings: Settings,
    frozen_clock: FrozenClock,
) -> Iterator[Gateway]:
    app = create_bank_app()

    def session_override() -> Iterator[DbSession]:
        yield bank_db
        bank_db.flush()

    app.dependency_overrides[get_session] = session_override
    bank = HttpBankClient(
        "http://bank",
        TEST_ENV["GATEWAY_SERVICE_TOKEN"],
        transport=httpx.ASGITransport(app=app),
        timeout=30,
    )
    yield Gateway(bank=bank, config=CONFIG)


async def _inbound(gw: Gateway) -> Session:
    req = SessionCreate(
        call_id=str(uuid.uuid4()), direction="inbound", campaign="inbound", caller_phone=UNKNOWN
    )
    return await gw.store.create(req, gw.bank)


async def test_unknown_number_inbound_verification(real_gateway: Gateway) -> None:
    s = await _inbound(real_gateway)
    assert s.candidate_ids == []
    claim = {"full_name": "Frank Fischer", "dob": FRANK.dob.isoformat(), "loan_last4": FRANK.loan_last4}
    r = await run_tool(real_gateway, "verify_identity", s.token, claim)
    assert r.data is not None and r.data.verified and r.data.first_name == "Frank"
    assert s.bound_customer_id == FRANK.customer_id


async def test_unknown_number_wrong_last4_is_a_failed_attempt(real_gateway: Gateway) -> None:
    s = await _inbound(real_gateway)
    claim = {"full_name": "Frank Fischer", "dob": FRANK.dob.isoformat(), "loan_last4": "0000"}
    r = await run_tool(real_gateway, "verify_identity", s.token, claim)
    assert r.data is not None and not r.data.verified and r.data.attempts_remaining == 1
    assert s.bound_customer_id is None
