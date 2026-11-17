"""T-AGENT-01 · AGENT-R1: the worker's call setup and teardown (the testable seam of the entrypoint)."""

import json

import httpx
import httpx2
import pytest

from supportagent.agent.worker import AGENT_NAME, setup_call, teardown_call
from supportagent.common.settings import Settings
from supportagent.contracts.outcome import Campaign, Direction
from tests.agent.test_gateway_client import TOKEN, FakeMcp, ok


async def test_setup_opens_the_session_then_the_gateway_client_and_teardown_closes_both(
    settings: Settings,
) -> None:
    requests: list[httpx.Request] = []

    def sessions(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"session_token": TOKEN, "expires_at": "2026-11-09T15:00:00Z"})
        return httpx.Response(204)

    fake = FakeMcp(replies={"lookup_by_phone": ok({"match": False})})
    server = fake.server()
    app = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0"
    )
    room_metadata = json.dumps({"direction": "outbound", "campaign": "missed_payment", "loan_id": 17})
    async with server.session_manager.run():
        state, gateway = await setup_call(
            settings,
            room_metadata,
            http_transport=httpx.MockTransport(sessions),
            mcp_transport=httpx2.ASGITransport(app=app),
        )
        assert (state.metadata.direction, state.metadata.campaign) == (
            Direction.outbound,
            Campaign.missed_payment,
        )
        assert state.session_token.get_secret_value() == TOKEN and TOKEN not in repr(state)
        result = await state.gateway.lookup_by_phone()
        assert result.ok and fake.headers == [f"Bearer {TOKEN}"]
        await teardown_call(settings, state, gateway)

    assert [r.method for r in requests][:1] == ["POST"]
    body = json.loads(requests[0].content)
    assert body["call_id"] == state.call_id and body["loan_id"] == 17
    assert requests[0].headers["authorization"].startswith("Bearer ")


def test_the_worker_registers_as_support_agent() -> None:
    assert AGENT_NAME == "support-agent"


async def test_finish_call_finalizes_before_teardown_and_tears_down_even_if_finalize_fails(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import SecretStr

    from supportagent.agent import worker
    from supportagent.agent.context import CallState
    from supportagent.agent.testing import FakeGateway
    from supportagent.contracts.call import CallMetadata

    order: list[str] = []

    async def fake_finalize(state: CallState, *, settings: Settings) -> None:
        order.append("finalize")
        if state.metadata.campaign is Campaign.playground:
            raise RuntimeError("boom with a secret")

    async def fake_teardown(settings: Settings, state: CallState, gateway: object) -> None:
        order.append("teardown")

    monkeypatch.setattr(worker, "finalize", fake_finalize)
    monkeypatch.setattr(worker, "teardown_call", fake_teardown)

    def state(campaign: Campaign) -> CallState:
        meta = CallMetadata(
            call_id="b5b6d2c4-6f7e-4c5a-9a8e-1d2c3b4a5f60", direction=Direction.inbound, campaign=campaign
        )
        return CallState(metadata=meta, gateway=FakeGateway(), session_token=SecretStr("t"))

    await worker.finish_call(settings, state(Campaign.inbound), object())  # type: ignore[arg-type]
    assert order == ["finalize", "teardown"]
    order.clear()
    await worker.finish_call(settings, state(Campaign.playground), object())  # type: ignore[arg-type]
    assert order == ["finalize", "teardown"]
