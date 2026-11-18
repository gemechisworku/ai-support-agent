"""Test doubles and a text-mode scenario runner for conversation states (no audio, no network, no model).

- `ScriptedLLM`: a LiveKit `llm.LLM` that answers from a script. Each LLM request (the model is asked once per
  user turn, and again after each round of tool results) consumes the next step: say something, call tools,
  or both. A step can also be a function of the chat so far, for conditional scripts.
- `FakeGateway`: a `GatewayPort` with canned results per tool, a call log, and a simple verification rule
  against a `PersonaCard` (two failed attempts lock the call). T-EVAL-01 extends it with full persona data.
- `FakeTransferer`: a `Transferer` that records the transfer and succeeds or fails on demand.
- `run_text_scenario`: plays the caller's turns against a starting state and returns what happened.

::

    llm = ScriptedLLM([
        say("Hello, this is Ava."),                                   # reply to turn 1
        call("verify_identity", full_name="Frank Alvarez", dob="1980-01-02", loan_last4="4417"),
        say("Thanks, you're verified."),                              # reply after the tool result
    ])
    result = await run_text_scenario(persona, ["hi", "Frank Alvarez, 2 Jan 1980, 4417"],
                                     agent=VerifyAgent(), llm=llm, gateway=FakeGateway(persona))
    result.transcript, result.tool_calls, result.outcome
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from livekit.agents import Agent, AgentSession, RunResult
from livekit.agents.llm import (
    LLM,
    ChatChunk,
    ChatContext,
    ChoiceDelta,
    FunctionToolCall,
    LLMStream,
    Tool,
)
from livekit.agents.llm.tool_context import get_fnc_tool_names
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from pydantic import SecretStr

from supportagent.agent.context import CallState
from supportagent.agent.transfer import TransferResult
from supportagent.contracts.bank import PersonaCard, ProductCode, TimeWindow
from supportagent.contracts.call import CallMetadata
from supportagent.contracts.gateway import (
    ContactFlag,
    ContactFlagOutput,
    EscalateOutput,
    EscalationReason,
    InteractionHistoryOutput,
    LoanStatusOutput,
    LookupByPhoneOutput,
    OutcomeRecordedOutput,
    RecordPromiseOutput,
    SearchPolicyOutput,
    TaskCreatedOutput,
    ToolResult,
    TransactionsOutput,
    VerifyIdentityOutput,
)
from supportagent.contracts.outcome import CallOutcome, Campaign, Direction

# --------------------------------------------------------------------------- scripted LLM


@dataclass(frozen=True)
class ToolCallSpec:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Step:
    """One scripted LLM response: optional speech, then optional tool calls."""

    text: str = ""
    calls: tuple[ToolCallSpec, ...] = ()


def say(text: str) -> Step:
    return Step(text=text)


def call(name: str, **arguments: Any) -> Step:
    return Step(calls=(ToolCallSpec(name, arguments),))


def say_and_call(text: str, name: str, **arguments: Any) -> Step:
    return Step(text=text, calls=(ToolCallSpec(name, arguments),))


ScriptItem = Step | Callable[[ChatContext], Step]


class ScriptExhausted(AssertionError):
    """The state asked the model for more responses than the scenario scripted."""


class ScriptedLLM(LLM):
    """Answers from `steps`, in order. `requests[i]` lists the tool names offered on the i-th request."""

    def __init__(self, steps: Sequence[ScriptItem]) -> None:
        super().__init__()
        self._steps = list(steps)
        self._next = 0
        self.requests: list[tuple[str, ...]] = []

    @property
    def model(self) -> str:
        return "scripted"

    @property
    def provider(self) -> str:
        return "scripted"

    @property
    def remaining(self) -> int:
        """Scripted steps not used yet (a finished scenario should have none)."""
        return len(self._steps) - self._next

    def take(self, chat_ctx: ChatContext, tools: Sequence[Tool]) -> Step:
        self.requests.append(tuple(sorted(get_fnc_tool_names(tools))))
        if self._next >= len(self._steps):
            raise ScriptExhausted(
                f"the LLM was asked for response #{self._next + 1} but only {len(self._steps)} were scripted"
            )
        item = self._steps[self._next]
        self._next += 1
        return item(chat_ctx) if callable(item) else item

    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools: list[Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        **_: Any,
    ) -> LLMStream:
        return _ScriptedStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


class _ScriptedStream(LLMStream):
    def __init__(
        self, llm: ScriptedLLM, *, chat_ctx: ChatContext, tools: list[Tool], conn_options: APIConnectOptions
    ) -> None:
        super().__init__(llm, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._scripted = llm

    async def _run(self) -> None:
        step = self._scripted.take(self._chat_ctx, self._tools)
        chunk_id = uuid.uuid4().hex
        if step.text:
            self._event_ch.send_nowait(
                ChatChunk(id=chunk_id, delta=ChoiceDelta(role="assistant", content=step.text))
            )
        if step.calls:
            calls = [
                FunctionToolCall(
                    name=c.name,
                    arguments=json.dumps(dict(c.arguments)),
                    call_id=f"call_{uuid.uuid4().hex[:12]}",
                )
                for c in step.calls
            ]
            self._event_ch.send_nowait(
                ChatChunk(id=chunk_id, delta=ChoiceDelta(role="assistant", tool_calls=calls))
            )


# --------------------------------------------------------------------------- fake gateway


@dataclass(frozen=True)
class GatewayCall:
    tool: str
    arguments: Mapping[str, Any]


Canned = ToolResult[Any] | Callable[..., ToolResult[Any]]
MAX_VERIFY_ATTEMPTS = 2


class FakeGateway:
    """A `GatewayPort` for tests: canned results by tool name, every call logged.

    `results["get_loan_status"]` is a `ToolResult` (or a function of the call's keyword arguments). A tool
    with
    nothing canned returns `upstream_error`, so an unexpected call is visible rather than silently fine.
    `verify_identity` checks the persona's name, date of birth and loan last 4 itself.
    """

    def __init__(
        self, persona: PersonaCard | None = None, results: Mapping[str, Canned] | None = None
    ) -> None:
        self.persona = persona
        self.results: dict[str, Canned] = dict(results or {})
        self.calls: list[GatewayCall] = []
        self.outcomes: list[CallOutcome] = []
        self.failed_verifications = 0

    @property
    def tools_called(self) -> list[str]:
        return [c.tool for c in self.calls]

    def _canned(self, tool: str, **arguments: Any) -> ToolResult[Any]:
        self.calls.append(GatewayCall(tool, arguments))
        canned = self.results.get(tool)
        if canned is None:
            return ToolResult.failure("upstream_error", f"no fake result for {tool}")
        return canned(**arguments) if callable(canned) else canned

    async def lookup_by_phone(self) -> ToolResult[LookupByPhoneOutput]:
        return self._canned("lookup_by_phone")

    async def verify_identity(
        self, full_name: str, dob: dt.date, loan_last4: str
    ) -> ToolResult[VerifyIdentityOutput]:
        self.calls.append(
            GatewayCall("verify_identity", {"full_name": full_name, "dob": dob, "loan_last4": loan_last4})
        )
        p = self.persona
        if self.failed_verifications >= MAX_VERIFY_ATTEMPTS:
            return ToolResult.failure("locked", "too many failed attempts")
        if (
            p
            and full_name.strip().casefold() == p.full_name.casefold()
            and dob == p.dob
            and loan_last4 == p.loan_last4
        ):
            first = p.full_name.split()[0]
            return ToolResult.success(
                VerifyIdentityOutput(
                    verified=True,
                    attempts_remaining=MAX_VERIFY_ATTEMPTS - self.failed_verifications,
                    locked=False,
                    first_name=first,
                )
            )
        self.failed_verifications += 1
        left = MAX_VERIFY_ATTEMPTS - self.failed_verifications
        return ToolResult.success(
            VerifyIdentityOutput(verified=False, attempts_remaining=left, locked=left == 0)
        )

    async def search_policy(
        self, query: str, product: ProductCode | None = None
    ) -> ToolResult[SearchPolicyOutput]:
        return self._canned("search_policy", query=query, product=product)

    async def get_loan_status(self, loan_last4: str | None = None) -> ToolResult[LoanStatusOutput]:
        return self._canned("get_loan_status", loan_last4=loan_last4)

    async def get_recent_transactions(self, loan_last4: str | None = None) -> ToolResult[TransactionsOutput]:
        return self._canned("get_recent_transactions", loan_last4=loan_last4)

    async def get_interaction_history(self) -> ToolResult[InteractionHistoryOutput]:
        return self._canned("get_interaction_history")

    async def record_promise_to_pay(
        self, promise_date: dt.date, amount: Decimal, loan_last4: str | None = None
    ) -> ToolResult[RecordPromiseOutput]:
        return self._canned(
            "record_promise_to_pay", promise_date=promise_date, amount=amount, loan_last4=loan_last4
        )

    async def request_callback(self, preferred_at: dt.datetime, reason: str) -> ToolResult[TaskCreatedOutput]:
        return self._canned("request_callback", preferred_at=preferred_at, reason=reason)

    async def escalate(self, reason: EscalationReason, summary: str) -> ToolResult[EscalateOutput]:
        return self._canned("escalate", reason=reason, summary=summary)

    async def set_contact_flag(
        self, flag: ContactFlag, preferred_window: TimeWindow | None = None, note: str = ""
    ) -> ToolResult[ContactFlagOutput]:
        return self._canned("set_contact_flag", flag=flag, preferred_window=preferred_window, note=note)

    async def record_call_outcome(self, outcome: CallOutcome) -> ToolResult[OutcomeRecordedOutput]:
        self.calls.append(GatewayCall("record_call_outcome", {"call_id": outcome.call_id}))
        self.outcomes.append(outcome)
        return ToolResult.success(OutcomeRecordedOutput(interaction_id=outcome.call_id))


# --------------------------------------------------------------------------- fake transferer


@dataclass(frozen=True)
class TransferCall:
    room: str
    participant_identity: str
    to_extension: str


class FakeTransferer:
    """A `Transferer` that records each transfer and succeeds, or fails with `detail` when `fail=True`."""

    def __init__(self, *, fail: bool = False, detail: str = "no_answer") -> None:
        self.fail = fail
        self.detail = detail
        self.transfers: list[TransferCall] = []

    async def transfer(self, room: str, participant_identity: str, to_extension: str) -> TransferResult:
        self.transfers.append(TransferCall(room, participant_identity, to_extension))
        return TransferResult(ok=False, detail=self.detail) if self.fail else TransferResult(ok=True)


# --------------------------------------------------------------------------- the scenario runner


@dataclass(frozen=True)
class TranscriptLine:
    role: str  # "caller" | "agent"
    text: str


@dataclass(frozen=True)
class ToolCallRecord:
    """A tool the model called, with the (string) result the model saw."""

    name: str
    arguments: dict[str, Any]
    output: str | None
    is_error: bool = False


@dataclass(frozen=True)
class ScenarioResult:
    transcript: list[TranscriptLine]
    tool_calls: list[ToolCallRecord]
    outcome: dict[str, Any]  # CallState.outcome_so_far(): replaced by the CallOutcome draft in T-AGENT-08
    states: list[str]
    gateway: FakeGateway
    llm: ScriptedLLM
    state: CallState

    @property
    def tool_names(self) -> list[str]:
        return [c.name for c in self.tool_calls]


def _call_state(
    persona: PersonaCard, gateway: FakeGateway, direction: Direction, campaign: Campaign
) -> CallState:
    meta = CallMetadata(
        call_id=str(uuid.uuid4()),
        direction=direction,
        campaign=campaign,
        customer_id=persona.customer_id,
        loan_id=persona.loan_id,
    )
    return CallState(metadata=meta, gateway=gateway, session_token=SecretStr("fake-session-token"))


async def run_text_scenario(
    persona: PersonaCard,
    turns: Sequence[str],
    *,
    agent: Agent,
    llm: ScriptedLLM,
    gateway: FakeGateway | None = None,
    direction: Direction = Direction.inbound,
    campaign: Campaign = Campaign.playground,
    on_start: Callable[[CallState], Awaitable[None]] | None = None,
) -> ScenarioResult:
    """Start `agent` in a text-only `AgentSession` and play the caller's `turns` against it.

    Returns the transcript (caller and agent lines in order), every tool call the model made with the
    result it saw, the call's outcome so far, and the states visited. Nothing here touches audio, the
    network or a real model. The agent's own entry speech (if it generates a reply on enter) consumes the
    first scripted step before the first caller turn.
    """
    gw = gateway or FakeGateway(persona)
    state = _call_state(persona, gw, direction, campaign)
    if on_start is not None:
        await on_start(state)
    transcript: list[TranscriptLine] = []
    records: list[ToolCallRecord] = []

    async with AgentSession[CallState](llm=llm, userdata=state) as session:
        start = await session.start(agent, capture_run=True)
        if start is not None:
            _collect(start.events, transcript, records)
        for turn in turns:
            transcript.append(TranscriptLine("caller", turn))
            run: RunResult[Any] = await session.run(user_input=turn)
            _collect(run.events, transcript, records)

    return ScenarioResult(
        transcript=transcript,
        tool_calls=records,
        outcome=state.outcome_so_far(),
        states=list(state.states_visited),
        gateway=gw,
        llm=llm,
        state=state,
    )


def _collect(events: Sequence[Any], transcript: list[TranscriptLine], records: list[ToolCallRecord]) -> None:
    pending: dict[str, int] = {}
    for event in events:
        item = event.item
        if event.type == "message" and item.role == "assistant":
            if item.text_content:
                transcript.append(TranscriptLine("agent", item.text_content))
        elif event.type == "function_call":
            try:
                args = json.loads(item.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": item.arguments}
            pending[item.call_id] = len(records)
            records.append(ToolCallRecord(item.name, args, None))
        elif event.type == "function_call_output" and item.call_id in pending:
            i = pending.pop(item.call_id)
            old = records[i]
            records[i] = ToolCallRecord(old.name, old.arguments, item.output, bool(item.is_error))
