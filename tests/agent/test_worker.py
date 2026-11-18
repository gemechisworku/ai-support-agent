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

    async def fake_finalize(state: CallState, *, settings: Settings, classify: object = None) -> None:
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


# --------------------------------------------------------------------------- T-AGENT-13: the session wiring


def test_prewarm_loads_only_the_vad_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from supportagent.agent import worker
    from supportagent.voice import providers

    loads: list[str] = []
    monkeypatch.setattr(providers, "build_vad", lambda: loads.append("vad") or "VAD")

    def no_job_context() -> object:
        raise RuntimeError("no job context found")  # what EnglishModel() does outside a job

    monkeypatch.setattr(providers, "build_turn_detector", no_job_context)
    proc = SimpleNamespace(userdata={})

    worker.prewarm(proc)  # type: ignore[arg-type]

    assert proc.userdata == {"vad": "VAD"}
    assert loads == ["vad"]  # once, in the process, not per call; the turn detector waits for the job
    assert worker.server.setup_fnc is worker.prewarm


async def test_run_call_wires_the_session_then_starts_it_and_finishes_the_call_once(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from livekit.agents import AgentSession
    from pydantic import SecretStr

    import supportagent.agent.states  # noqa: F401  (importing it registers every state)
    from supportagent.agent import worker
    from supportagent.agent.context import CallState
    from supportagent.agent.testing import FakeGateway, ScriptedLLM
    from supportagent.contracts.call import CallMetadata

    events: list[str] = []
    sessions: list[AgentSession[CallState]] = []

    def fake_build(
        settings: Settings, vad: object, state: CallState, **kwargs: object
    ) -> AgentSession[CallState]:
        events.append(f"build vad={vad} turn={kwargs.get('turn_detector')}")
        session = AgentSession[CallState](llm=ScriptedLLM([]), userdata=state)
        real_start = session.start

        async def start(agent: object, **kw: object) -> object:
            events.append(f"start room={kw.get('room', 'none')}")
            return await real_start(agent, **kw)  # type: ignore[arg-type]

        monkeypatch.setattr(session, "start", start)
        sessions.append(session)
        return session

    async def fake_finish(
        settings: Settings, state: CallState, gateway: object, *, classify: object = None
    ) -> None:
        events.append(f"finish classify={'yes' if callable(classify) else 'no'}")

    class Ctx:
        def __init__(self) -> None:
            self.callbacks: list[object] = []
            self.shutdowns: list[str] = []

        def add_shutdown_callback(self, callback: object) -> None:
            events.append("register shutdown")
            self.callbacks.append(callback)

        def shutdown(self, reason: str = "") -> None:
            self.shutdowns.append(reason)

    monkeypatch.setattr(worker, "build_agent_session", fake_build)
    monkeypatch.setattr(worker, "finish_call", fake_finish)
    meta = CallMetadata(
        call_id="b5b6d2c4-6f7e-4c5a-9a8e-1d2c3b4a5f61",
        direction=Direction.inbound,
        campaign=Campaign.playground,
    )
    state = CallState(metadata=meta, gateway=FakeGateway(), session_token=SecretStr("t"))
    ctx = Ctx()

    session = await worker.run_call(ctx, settings, state, object(), vad="V", turn_detector="T")  # type: ignore[arg-type]
    try:
        # the shutdown callback is registered before the session starts, so a failed start still tears down
        assert events[:3] == ["build vad=V turn=T", "register shutdown", "start room=none"]
        assert state.states_visited[:1] == ["greeting"]  # the entry state
        assert session is sessions[0]

        session.emit(
            "close", SimpleNamespace(reason="x")
        )  # the session closed: the job is ended through the context
        assert ctx.shutdowns == ["session closed"]

        for callback in ctx.callbacks:
            await callback()  # type: ignore[operator]
        assert [e for e in events if e.startswith("finish")] == ["finish classify=yes"]
    finally:
        await session.aclose()
