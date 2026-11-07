"""T-GW-04 · GW-R9: sliding-window rate limits, driven by the frozen clock."""

import uuid

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from supportagent.common.clock import FrozenClock
from supportagent.gateway.app import create_app
from supportagent.gateway.ratelimit import RateLimiter
from supportagent.gateway.tools import Gateway, ToolContext, ToolRegistry, run_tool
from tests.gateway.conftest import CONFIG, open_session
from tests.gateway.fakes import FRANK, FakeBank


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    ok: bool = True


REG = ToolRegistry()


async def _noop(ctx: ToolContext, inp: In) -> Out:
    return Out()


REG.tool("ping", scope="any", input_model=In, output_model=Out)(_noop)
REG.tool("search_policy", scope="any", input_model=In, output_model=Out)(_noop)


def test_window_slides(frozen_clock: FrozenClock) -> None:
    limiter = RateLimiter()
    assert all(limiter.allow("k", 3) for _ in range(3))
    assert not limiter.allow("k", 3)
    frozen_clock.advance(seconds=59)
    assert not limiter.allow("k", 3)
    frozen_clock.advance(seconds=1)
    assert limiter.allow("k", 3)  # the first three hits left the window


async def _codes(gw: Gateway, token: str, tool: str, n: int) -> list[str]:
    out = []
    for _ in range(n):
        r = await run_tool(gw, tool, token, {}, REG)
        out.append("ok" if r.ok else r.error.code if r.error else "?")
    return out


async def test_21st_call_in_a_minute_is_denied_and_audited(
    gateway: Gateway, bank: FakeBank, frozen_clock: FrozenClock
) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    codes = await _codes(gateway, s.token, "ping", 21)
    assert codes == ["ok"] * 20 + ["rate_limited"]
    assert bank.audit[-1].decision == "deny"
    frozen_clock.advance(minutes=1)
    assert await _codes(gateway, s.token, "ping", 1) == ["ok"]


async def test_151st_call_of_a_session_is_denied(gateway: Gateway, frozen_clock: FrozenClock) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    codes: list[str] = []
    for _ in range(8):  # 8 minutes x 19 calls = 152 calls, never above 20/min
        codes += await _codes(gateway, s.token, "ping", 19)
        frozen_clock.advance(minutes=1)
    assert codes[:150] == ["ok"] * 150
    assert codes[150:] == ["rate_limited"] * 2


async def test_search_policy_is_limited_to_10_per_minute(gateway: Gateway) -> None:
    s = await open_session(gateway, loan_id=FRANK.loan_id)
    assert await _codes(gateway, s.token, "search_policy", 11) == ["ok"] * 10 + ["rate_limited"]
    assert await _codes(gateway, s.token, "ping", 1) == ["ok"]  # other tools still allowed


async def test_limits_are_per_session(gateway: Gateway) -> None:
    a = await open_session(gateway, loan_id=FRANK.loan_id)
    b = await open_session(gateway, loan_id=FRANK.loan_id)
    await _codes(gateway, a.token, "ping", 20)
    assert await _codes(gateway, b.token, "ping", 1) == ["ok"]


def test_worker_credential_is_limited_to_120_per_minute(gateway: Gateway) -> None:
    client = TestClient(create_app(gateway))
    headers = {"Authorization": f"Bearer {CONFIG.worker_token}"}
    statuses = [
        client.post(
            "/sessions", json={"call_id": str(uuid.uuid4()), "direction": "inbound"}, headers=headers
        ).status_code
        for _ in range(121)
    ]
    assert statuses[:120] == [200] * 120
    assert statuses[120] == 429
