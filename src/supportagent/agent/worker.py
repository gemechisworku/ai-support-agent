"""The LiveKit Agents worker entrypoint `support-agent` (AGENT-R1).

    python -m supportagent.agent.worker dev      # registers with LiveKit and waits for jobs

On a job the worker: connects to the room; parses `CallMetadata` (room metadata, else SIP attributes of the
first participant, else a playground call); opens the gateway session from code (`POST /sessions` with the
worker credential); builds the `GatewayClient` and the `CallState` (the session's `userdata`); and registers
a shutdown callback that always closes the gateway client and the session.

The conversation states, prompts, outcome writing and the `AgentSession` wiring arrive in T-AGENT-02..08 and
T-VOICE-03; until then a call is set up and torn down but nobody speaks.
"""

from __future__ import annotations

import httpx
import httpx2
from livekit import agents

from supportagent.agent.context import CallState, parse_call_metadata
from supportagent.agent.gateway_client import GatewayClient, close_session, open_session
from supportagent.common.logging import bind_call, configure_logging, get_logger
from supportagent.common.settings import Settings, get_settings

AGENT_NAME = "support-agent"

log = get_logger(__name__)
server = agents.AgentServer()


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

    async def _finalize() -> None:
        await teardown_call(settings, state, gateway)

    ctx.add_shutdown_callback(_finalize)
    with bind_call(state.call_id):
        log.info(
            "call ready",
            extra={"direction": state.metadata.direction, "campaign": state.metadata.campaign},
        )


if __name__ == "__main__":
    configure_logging()
    agents.cli.run_app(server)
