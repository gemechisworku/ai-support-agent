"""`run_scenario`: play one scenario through the real agent state machine in text mode (EVAL-R1).

    record = await run_scenario(scenario, agent=lambda: MyEntryState())

The caller's turns are scripted; the model is a `ScriptedLLM` (or any LiveKit `llm.LLM` you pass); the data
comes from an in-memory `FakeGateway`. Nothing touches audio, the network or a database. Every timestamp and
id comes from the frozen clock and the scenario id, so running a scripted scenario twice gives the same
`RunRecord` JSON, byte for byte.

Only this file talks to LiveKit's text-mode API (through `agent.testing.run_text_scenario`), so a LiveKit
upgrade touches one place. The entry state for a direction and campaign is registered by T-AGENT-04; until
then pass `agent=` (the tests use small synthetic states).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from livekit.agents import Agent

from supportagent.agent import testing as base
from supportagent.agent.base import STATE_REGISTRY
from supportagent.agent.context import CallState
from supportagent.agent.prompts.registry import PromptRegistry
from supportagent.common.clock import freeze_clock, now
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.outcome import Campaign, Direction, Versions
from supportagent.contracts.transcript import TranscriptTurn
from supportagent.evals.fakes import (
    FakeBankData,
    FakeGateway,
    GatewayRules,
    PolicyFn,
    ScriptedLLM,
    follow_expected_tools,
)
from supportagent.evals.records import Mode, ModelCall, RunRecord, ToolCallLog

FROZEN_AT = "2026-11-09T14:00:00Z"
ENTRY_STATE = "greeting"  # the state T-AGENT-04 registers as every call's first state
POLICY_VERSION = "eval-fixture"


class NoEntryState(LookupError):
    """No entry state is registered yet, and the scenario run was not given an `agent` factory."""


@dataclass(frozen=True)
class Scenario:
    """One scripted scenario. T-EVAL-02 loads these from YAML; the fields are the same.

    `data` builds fresh bank records for each run (the fake gateway writes to them). `agent_script` replays
    the model's steps; without it `expected_tools` drives the default policy. `misbehave` makes the model do
    something wrong on purpose.
    """

    id: str
    persona: PersonaCard
    data: Callable[[], FakeBankData]
    caller_turns: Sequence[str]
    direction: Direction = Direction.inbound
    campaign: Campaign = Campaign.inbound
    loan_id: int | None = None  # outbound: the loan the call is about
    caller_phone: str | None = None  # inbound: the number the call came from
    agent_script: Sequence[base.ScriptItem] | None = None
    expected_tools: Sequence[base.ToolCallSpec] = ()
    misbehave: str | None = None
    policy: PolicyFn | None = None
    rules: GatewayRules = field(default_factory=GatewayRules)


def scenario_call_id(scenario_id: str) -> str:
    """A UUID4-shaped call id derived from the scenario id: the same scenario always gets the same id."""
    digest = bytearray(hashlib.sha256(scenario_id.encode()).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def default_entry_agent() -> Agent:
    """The registered entry state (`greeting`); raises `NoEntryState` if it is not registered."""
    import supportagent.agent.states  # noqa: F401  (importing registers every state)

    cls = STATE_REGISTRY.get(ENTRY_STATE)
    if cls is None:
        raise NoEntryState(f"no '{ENTRY_STATE}' state is registered; pass agent=<factory> to run_scenario")
    return cls()


async def run_scenario(
    scenario: Scenario,
    *,
    mode: Mode = "scripted",
    llm: ScriptedLLM | None = None,
    gateway: FakeGateway | None = None,
    agent: Callable[[], Agent] | None = None,
) -> RunRecord:
    """Run `scenario` once and return everything it left behind."""
    if mode != "scripted":
        raise NotImplementedError("simulated callers arrive with T-EVAL-04; use mode='scripted'")
    call_id = scenario_call_id(scenario.id)
    with freeze_clock(FROZEN_AT):
        gw = gateway or FakeGateway(
            scenario.data(),
            persona=scenario.persona,
            direction=scenario.direction,
            campaign=scenario.campaign,
            loan_id=scenario.loan_id,
            caller_phone=scenario.caller_phone,
            call_id=call_id,
            rules=scenario.rules,
            policy=scenario.policy,
        )
        model = llm or ScriptedLLM(
            scenario.agent_script,
            policy=follow_expected_tools(scenario.expected_tools),
            misbehave=scenario.misbehave,
        )

        async def on_start(state: CallState) -> None:
            state.metadata = state.metadata.model_copy(
                update={
                    "call_id": call_id,
                    "loan_id": scenario.loan_id,
                    "caller_phone": scenario.caller_phone,
                    "customer_id": None,  # the gateway binds identity; the call never knows it up front
                }
            )

        result = await base.run_text_scenario(
            scenario.persona,
            scenario.caller_turns,
            agent=(agent or default_entry_agent)(),
            llm=model,
            gateway=gw,
            direction=scenario.direction,
            campaign=scenario.campaign,
            on_start=on_start,
        )
        versions = Versions(
            prompt=PromptRegistry().bundle_version(),
            policy=POLICY_VERSION,
            model=f"{model.provider}/{model.model}",
        )
        outcome = gw.outcomes[-1] if gw.outcomes else gw.draft_outcome(versions)
        return RunRecord(
            scenario_id=scenario.id,
            mode=mode,
            call_id=call_id,
            turns=_turns(call_id, result.transcript),
            tool_calls=_gateway_calls(gw),
            model_calls=[
                ModelCall(seq=i, name=c.name, arguments=c.arguments, is_error=c.is_error)
                for i, c in enumerate(result.tool_calls, 1)
            ],
            audit=list(gw.audit),
            outcome=outcome,
            bank_snapshot=gw.data.snapshot(),
            llm_model=versions.model,
            versions=versions,
            states=result.states,
        )


def _turns(call_id: str, lines: Sequence[base.TranscriptLine]) -> list[TranscriptTurn]:
    start = now()
    return [
        TranscriptTurn(
            call_id=call_id,
            seq=i,
            ts=start + dt.timedelta(seconds=i),
            speaker="agent" if line.role == "agent" else "caller",
            text=line.text,
        )
        for i, line in enumerate(lines, 1)
    ]


def _json_args(args: Any) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(json.dumps(args, default=str))
    return loaded


def _gateway_calls(gw: FakeGateway) -> list[ToolCallLog]:
    logs = []
    for i, (called, row, response) in enumerate(zip(gw.calls, gw.audit, gw.responses, strict=True), 1):
        logs.append(
            ToolCallLog(
                seq=i,
                name=called.tool,
                arguments=_json_args(called.arguments),
                ok=response.ok,
                error_code=response.error.code if response.error else None,
                decision=row.decision,
            )
        )
    return logs
