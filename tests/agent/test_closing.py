"""T-AGENT-08 · AGENT-R11: the closing state reads back commitments, offers a callback and ends the call.

Text mode with the scripted LLM: no audio, no network, no model.
"""

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from livekit.agents import AgentSession
from pydantic import SecretStr

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent.base import STATE_REGISTRY, check_registered_states
from supportagent.agent.context import CallState
from supportagent.agent.finalize import attach_recorder, finalize
from supportagent.agent.states import flow
from supportagent.agent.states.closing import ClosingAgent, goodbye_line
from supportagent.agent.testing import FakeGateway, ScriptedLLM, call, run_text_scenario, say
from supportagent.common.settings import Settings
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.call import CallMetadata
from supportagent.contracts.gateway import SearchPolicyOutput, TaskCreatedOutput, ToolResult
from supportagent.contracts.outcome import Campaign, ContactResult, Direction, Intent, Versions
from supportagent.observability.transcripts import TranscriptStore

FRANK = PersonaCard(
    customer_id=9001,
    full_name="Frank Alvarez",
    phone="+15550100001",
    extension="1001",
    dob=dt.date(1980, 1, 2),
    loan_id=1,
    loan_last4="4417",
    product_code="PERSONAL",
    situation="synthetic test persona",
    expected_outcome="n/a",
)
PROMISE_DATE = dt.date(2026, 11, 12)
CALLBACK_AT = dt.datetime(2026, 11, 13, 14, 30, tzinfo=dt.UTC)
VERSIONS = Versions(prompt="0.1.0+abc", policy="p-1", model="llm:llamacpp/test")
SOURCE = {
    "doc_id": "payment-channels",
    "version": "1.0",
    "section": "Ways to pay",
    "text": "You can pay in a branch, in the mobile app, or by bank transfer.",
    "score": 0.9,
}
CHANNELS = ToolResult.success(
    SearchPolicyOutput.model_validate({"chunks": [SOURCE], "low_confidence": False})
)
NO_CHANNELS = ToolResult.success(SearchPolicyOutput.model_validate({"chunks": [], "low_confidence": True}))


def _verified(state: CallState) -> None:
    state.verified, state.contact_result = True, ContactResult.right_party


async def _promised(state: CallState) -> None:
    _verified(state)
    state.outcome.record_promise(PROMISE_DATE, Decimal("200.72"))


def _task(kind: str = "callback") -> ToolResult[TaskCreatedOutput]:
    return ToolResult.success(
        TaskCreatedOutput.model_validate({"task_id": 7, "type": kind, "due_at": CALLBACK_AT.isoformat()})
    )


def test_the_closing_state_is_registered_with_two_tools() -> None:
    assert STATE_REGISTRY[flow.CLOSING] is ClosingAgent
    check_registered_states()
    from supportagent.agent.base import class_tool_names

    assert class_tool_names(ClosingAgent) == ["end_call", "offer_callback", "report_concern"]


def test_the_goodbye_line_is_the_bank_s_wording() -> None:
    assert goodbye_line("Meridian Bank") == "Thank you for calling Meridian Bank. Goodbye."


async def test_it_reads_back_the_promise_then_asks_if_there_is_anything_else() -> None:
    llm = ScriptedLLM([say("Is there anything else I can help you with?"), say("Goodbye."), say("")])
    gateway = FakeGateway(FRANK, {"search_policy": CHANNELS})
    result = await run_text_scenario(
        FRANK,
        [],
        agent=ClosingAgent(),
        llm=llm,
        gateway=gateway,
        on_start=_promised,
        direction=Direction.outbound,
        campaign=Campaign.missed_payment,
    )
    spoken = [line.text for line in result.transcript if line.role == "agent"]
    assert spoken[0] == "To confirm: you will pay $200.72 by 12 November 2026."
    assert spoken[1] == "Is there anything else I can help you with?"
    assert "search_policy" in gateway.tools_called  # payment channels come from policy
    assert result.state.outcome.kb_sources == ["payment-channels@1.0#Ways to pay"]


async def test_the_policy_text_reaches_the_model_as_data_and_nothing_else_is_made_up() -> None:
    seen: list[str] = []

    def step(ctx: Any) -> Any:
        seen.append(json.dumps([getattr(m, "text_content", "") for m in ctx.items], default=str))
        return say("You can pay in a branch, in the app or by transfer. Anything else?")

    llm = ScriptedLLM([step])
    await run_text_scenario(
        FRANK,
        [],
        agent=ClosingAgent(),
        llm=llm,
        gateway=FakeGateway(FRANK, {"search_policy": CHANNELS}),
        on_start=_promised,
    )
    assert "branch, in the mobile app, or by bank transfer" in seen[0]


async def test_without_a_commitment_there_is_no_readback_and_no_policy_lookup() -> None:
    llm = ScriptedLLM([say("Is there anything else I can help you with?")])
    gateway = FakeGateway(FRANK)
    result = await run_text_scenario(
        FRANK, [], agent=ClosingAgent(), llm=llm, gateway=gateway, on_start=_verified_only
    )
    spoken = [line.text for line in result.transcript if line.role == "agent"]
    assert spoken == ["Is there anything else I can help you with?"]
    assert gateway.tools_called == []


async def _verified_only(state: CallState) -> None:
    _verified(state)


async def test_a_low_confidence_policy_result_adds_no_source() -> None:
    llm = ScriptedLLM([say("Anything else?")])
    result = await run_text_scenario(
        FRANK,
        [],
        agent=ClosingAgent(),
        llm=llm,
        gateway=FakeGateway(FRANK, {"search_policy": NO_CHANNELS}),
        on_start=_promised,
    )
    assert result.state.outcome.kb_sources == []


async def test_the_caller_can_end_the_call() -> None:
    llm = ScriptedLLM([say("Anything else?"), call("end_call"), say("Thank you, goodbye.")])
    result = await run_text_scenario(
        FRANK,
        ["No, that's all."],
        agent=ClosingAgent(),
        llm=llm,
        gateway=FakeGateway(FRANK, {"search_policy": CHANNELS}),
        on_start=_promised,
    )
    assert result.tool_names == ["end_call"]


async def test_offer_callback_schedules_it_through_the_gateway_and_records_it() -> None:
    llm = ScriptedLLM(
        [
            say("Anything else?"),
            call("offer_callback", when="2026-11-13T14:30:00+00:00", reason="wants a call tomorrow"),
            say("I have booked that."),
        ]
    )
    gateway = FakeGateway(FRANK, {"request_callback": _task()})
    result = await run_text_scenario(
        FRANK,
        ["Could you call me tomorrow afternoon?"],
        agent=ClosingAgent(),
        llm=llm,
        gateway=gateway,
        on_start=_verified_only,
    )
    assert gateway.tools_called == ["request_callback"]
    assert result.state.outcome.callback is not None
    assert result.state.outcome.callback.datetime == CALLBACK_AT
    assert Intent.callback_request in result.state.outcome.intents


async def test_a_callback_time_without_a_timezone_is_refused_and_not_recorded() -> None:
    llm = ScriptedLLM(
        [
            say("Anything else?"),
            call("offer_callback", when="2026-11-13T14:30:00", reason="call me"),
            say("Which time exactly?"),
        ]
    )
    gateway = FakeGateway(FRANK, {"request_callback": _task()})
    result = await run_text_scenario(
        FRANK, ["call me tomorrow"], agent=ClosingAgent(), llm=llm, gateway=gateway, on_start=_verified_only
    )
    assert gateway.tools_called == [] and result.state.outcome.callback is None
    assert result.tool_calls[0].is_error is False  # an answer for the model, not a crash


async def test_a_gateway_failure_scheduling_the_callback_is_not_recorded() -> None:
    llm = ScriptedLLM(
        [
            say("Anything else?"),
            call("offer_callback", when="2026-11-13T14:30:00+00:00", reason="call me"),
            say("I could not book that; someone will follow up."),
        ]
    )
    gateway = FakeGateway(FRANK, {"request_callback": ToolResult.failure("not_verified", "no")})
    result = await run_text_scenario(FRANK, ["call me"], agent=ClosingAgent(), llm=llm, gateway=gateway)
    assert result.state.outcome.callback is None


# ------------------------------------------------------- end to end: closing + recorder + finalize


@pytest.mark.parametrize("dropped", [False, True])
async def test_a_call_through_the_closing_state_ends_with_one_outcome(
    settings: Settings, tmp_path: Any, dropped: bool
) -> None:
    gateway = FakeGateway(FRANK, {"search_policy": CHANNELS})
    meta = CallMetadata(
        call_id=str(uuid.uuid4()),
        direction=Direction.outbound,
        campaign=Campaign.missed_payment,
        customer_id=9001,
        loan_id=1,
    )
    state = CallState(metadata=meta, gateway=gateway, session_token=SecretStr("fake-session-token"))
    await _promised(state)
    llm = ScriptedLLM([say("Anything else?"), say("Of course."), say("")])
    async with AgentSession[CallState](llm=llm, userdata=state) as session:
        attach_recorder(session, state)
        await session.start(ClosingAgent())
        if not dropped:
            await session.run(user_input="That is all, thanks.")
    store = TranscriptStore(tmp_path)
    result = await finalize(state, settings=settings, store=store, versions=VERSIONS, retry_delay_s=0)

    assert result is not None and result.recorded
    assert gateway.tools_called.count("record_call_outcome") == 1
    out = gateway.outcomes[0]
    assert out.promise_to_pay is not None and out.verified and out.transcript_uri == store.uri(state.call_id)
    assert "$200.72" in out.summary and out.kb_sources == ["payment-channels@1.0#Ways to pay"]
    if not dropped:  # a call cut off before the reply finished has whatever was said so far
        spoken = [t.text for t in store.read(state.call_id) if t.speaker == "agent"]
        assert spoken[0].startswith("To confirm: you will pay $200.72")
