"""Gateway unit fixtures: a `Gateway` over `FakeBank`, and helpers to open sessions."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from supportagent.common.clock import FrozenClock
from supportagent.contracts.gateway import SessionCreate
from supportagent.gateway.config import GatewayConfig
from supportagent.gateway.session import Session
from supportagent.gateway.tools import Gateway
from tests.gateway.fakes import FakeBank

CONFIG = GatewayConfig(worker_token="worker-tok", service_token="service-tok", audit_salt="salt")


@pytest.fixture
def bank() -> FakeBank:
    return FakeBank()


@pytest.fixture
def gateway(bank: FakeBank, frozen_clock: FrozenClock) -> Iterator[Gateway]:
    yield Gateway(bank=bank, config=CONFIG)


async def open_session(gw: Gateway, **kw: Any) -> Session:
    values: dict[str, Any] = {
        "call_id": str(uuid.uuid4()),
        "direction": "outbound",
        "campaign": "missed_payment",
    }
    values.update(kw)
    return await gw.store.create(SessionCreate(**values), gw.bank)
