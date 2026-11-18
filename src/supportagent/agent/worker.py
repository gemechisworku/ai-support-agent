"""The LiveKit Agents worker entrypoint `support-agent` (AGENT-R1).

    python -m supportagent.agent.worker dev      # registers with LiveKit and waits for jobs

Once per worker process, `prewarm` loads the Silero VAD (seconds, so never per call). The turn detector
cannot be built there: LiveKit's `EnglishModel` needs a running job (it runs in the shared inference
process), so the entrypoint builds it per call, which is cheap. On a job the worker:
- connects to the room and parses `CallMetadata` (room metadata, else SIP attributes of the first
  participant, else a playground call);
- opens the gateway session from code (`POST /sessions` with the worker credential) and builds the
  `GatewayClient` and the `CallState` (the session's `userdata`);
- `run_call` then builds the `AgentSession` (`session_factory`), registers the shutdown callback and starts
  the entry state, the greeting, which speaks the recording disclosure first.

While the call runs, the recorder keeps the turns, the silence policy re-prompts once and then ends a
silent call with the goodbye line, and the states do the talking. When the session closes the job is ended.
At the end of every call, however it ends, the shutdown callback runs `finish_call`: `finalize` classifies the
sentiment, writes the redacted transcript and exactly one outcome (T-AGENT-08), then the gateway client and
the session are closed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import httpx
import httpx2
from livekit import agents

from supportagent.agent.context import CallState, parse_call_metadata
from supportagent.agent.finalize import SentimentClassifier, finalize
from supportagent.agent.gateway_client import GatewayClient, close_session, open_session
from supportagent.agent.session_factory import build_agent_session, entry_agent, wire_call
from supportagent.common.logging import bind_call, configure_logging, get_logger
from supportagent.common.settings import Settings, get_settings
from supportagent.voice import providers

AGENT_NAME = "support-agent"

log = get_logger(__name__)
# A process starts when a job arrives (dev mode keeps no idle ones): leave time to load the VAD in `prewarm`.
server = agents.AgentServer(initialize_process_timeout=60.0)


def prewarm(proc: agents.JobProcess) -> None:
    """Once per worker process: load the Silero VAD so no call pays for it.

    The turn detector is not loaded here: `EnglishModel()` needs a job context, which does not exist yet.
    """
    proc.userdata["vad"] = providers.build_vad()


server.setup_fnc = prewarm


class CallContext(Protocol):
    """The part of LiveKit's `JobContext` that `run_call` needs (a fake in tests)."""

    def add_shutdown_callback(self, callback: Callable[[], Any]) -> None: ...

    def shutdown(self, reason: str = ...) -> None: ...


async def setup_call(
    settings: Settings,
    room_metadata: str | None,
    sip_attributes: dict[str, str] | None = None,
    *,
    http_transport: httpx.AsyncBaseTransport | None = None,
    mcp_transport: httpx2.AsyncBaseTransport | None = None,
) -> tuple[CallState, GatewayClient]:
    """Parse the call context, open the gateway session and connect the MCP client (a testable seam)."""
    meta = parse_call_metadata(room_metadata, sip_attributes)
    services = settings.services
    token = await open_session(
        services.gateway_url, services.gateway_worker_token, meta, transport=http_transport
    )
    gateway = await GatewayClient(services.gateway_url, token, transport=mcp_transport).__aenter__()
    return CallState(metadata=meta, gateway=gateway, session_token=token), gateway


async def teardown_call(settings: Settings, state: CallState, gateway: GatewayClient) -> None:
    """Always runs at the end of a call: close the MCP client, then the gateway session (idempotent)."""
    await gateway.__aexit__(None, None, None)
    services = settings.services
    await close_session(services.gateway_url, services.gateway_worker_token, state.call_id)


async def finish_call(
    settings: Settings,
    state: CallState,
    gateway: GatewayClient,
    *,
    classify: SentimentClassifier | None = None,
) -> None:
    """The end of a call: write the transcript and the one outcome, then always tear the call down."""
    try:
        await finalize(state, settings=settings, classify=classify)
    except Exception as exc:  # never let a failed outcome skip the teardown; the message may hold call data
        log.error("finalize failed", extra={"error": type(exc).__name__})
    finally:
        await teardown_call(settings, state, gateway)


async def run_call(
    ctx: CallContext,
    settings: Settings,
    state: CallState,
    gateway: GatewayClient,
    *,
    vad: Any,
    turn_detector: Any,
    room: Any | None = None,
) -> agents.AgentSession[CallState]:
    """Build the session, wire it, register the shutdown callback, then start the entry state.

    The shutdown callback is registered before the session starts, so a call that fails to start is still
    finalized and torn down. `room=None` starts a text-only session (the tests' seam).
    """
    session = build_agent_session(settings, vad, state, turn_detector=turn_detector)
    wiring = wire_call(session, state, settings, on_close=lambda: ctx.shutdown("session closed"))

    async def _finish() -> None:
        wiring.stop()
        wiring.apply_latency(state)
        await finish_call(settings, state, gateway, classify=wiring.classify)

    ctx.add_shutdown_callback(_finish)
    if room is None:
        await session.start(entry_agent(settings))
    else:
        await session.start(entry_agent(settings), room=room)
    return session


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: agents.JobContext) -> None:
    settings = get_settings()
    await ctx.connect()
    room_metadata = ctx.room.metadata or ctx.job.metadata or None
    attributes: dict[str, str] = {}
    if not room_metadata:  # inbound SIP: the caller's attributes carry the number
        participant = await ctx.wait_for_participant()
        attributes = dict(participant.attributes)

    state, gateway = await setup_call(settings, room_metadata, attributes)
    with bind_call(state.call_id):
        log.info(
            "call ready",
            extra={"direction": state.metadata.direction, "campaign": state.metadata.campaign},
        )
        await run_call(
            ctx,
            settings,
            state,
            gateway,
            vad=ctx.proc.userdata["vad"],
            turn_detector=providers.build_turn_detector(),  # needs the job context (inference executor)
            room=ctx.room,
        )


if __name__ == "__main__":
    configure_logging()
    agents.cli.run_app(server)
