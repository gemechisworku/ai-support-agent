"""T-AGENT-05 · AGENT-R1: inbound routing by intent, in text mode with the scripted LLM.

The states a caller is routed to (policy, account, escalation) belong to later tasks, so the tests register
stand-ins under the names `flow` uses. The stand-ins have a `finish` tool that returns to the router, as the
real ones will.
"""

import datetime as dt
import re
import shutil
from pathlib import Path
from typing import ClassVar

import pytest
from livekit.agents import Agent, RunContext, function_tool
from pydantic import SecretStr

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent.base import STATE_REGISTRY, BaseStateAgent, check_registered_states, class_tool_names
from supportagent.agent.context import CallState
from supportagent.agent.prompts.registry import DEFAULT_PROMPTS_DIR, PromptRegistry
from supportagent.agent.states import flow
from supportagent.agent.states.closing import goodbye_line
from supportagent.agent.states.greeting import GreetingAgent
from supportagent.agent.states.router import ANYTHING_ELSE, HOW_CAN_I_HELP, RouterAgent
from supportagent.agent.testing import FakeGateway, ScenarioResult, ScriptedLLM, call, run_text_scenario, say
from supportagent.compliance.disclosures import load_disclosures
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.call import CallMetadata
from supportagent.contracts.gateway import TaskCreatedOutput, ToolResult
from supportagent.contracts.outcome import Campaign, Direction, Intent

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_disclosures(ROOT / "data" / "config" / "disclosures.yaml")
VARS = {"bank_name": "Meridian Bank", "agent_name": "Ava"}
STAND_IN_NAMES = ("policy", "account", "escalation")

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
VERIFY_FRANK = call("verify_identity", full_name="Frank Alvarez", dob="1980-01-02", loan_last4="4417")


class _StandIn(BaseStateAgent):
    """A later task's state: records that it was entered (with its keyword arguments) and can finish."""

    entered: ClassVar[list[tuple[str, dict[str, object]]]] = []

    def __init__(self, **kwargs: object) -> None:
        extra = {k: v for k, v in kwargs.items() if k not in {"prompts", "variables", "chat_ctx", "tools"}}
        base = {k: v for k, v in kwargs.items() if k not in extra}
        super().__init__(**base)  # type: ignore[arg-type]
        _StandIn.entered.append((self.state_name, extra))

    @function_tool
    async def finish(self, context: RunContext[CallState]) -> Agent:
        """Go back to the router (what the real account and policy states do when they are done)."""
        router = flow.resolve(flow.ROUTER)
        assert router is not None
        return self.next_state(router)


class _PolicyStandIn(_StandIn):
    state_name = "policy"


class _AccountStandIn(_StandIn):
    state_name = "account"


class _EscalationStandIn(_StandIn):
    state_name = "escalation"


@pytest.fixture
def prompts(tmp_path: Path) -> PromptRegistry:
    """The real prompts plus a placeholder prompt for each stand-in state."""
    d = tmp_path / "prompts"
    shutil.copytree(DEFAULT_PROMPTS_DIR, d)
    for name in STAND_IN_NAMES:
        (d / f"{name}.md").write_text("---\nversion: '1'\n---\nA later task's state.\n")
    return PromptRegistry(d)


@pytest.fixture
def stand_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    _StandIn.entered = []
    for cls in (_PolicyStandIn, _AccountStandIn, _EscalationStandIn):
        monkeypatch.setitem(STATE_REGISTRY, cls.state_name, cls)


async def _run(
    prompts: PromptRegistry,
    steps: list,
    turns: list[str],
    *,
    verified: bool = False,
    start: BaseStateAgent | None = None,
    direction: Direction = Direction.inbound,
    campaign: Campaign = Campaign.inbound,
    results: dict | None = None,
) -> ScenarioResult:
    async def on_start(state: CallState) -> None:
        state.disclosures = REGISTRY
        state.verified = verified

    return await run_text_scenario(
        FRANK,
        turns,
        agent=start or RouterAgent(prompts=prompts, variables=VARS),
        llm=ScriptedLLM(steps),
        gateway=FakeGateway(FRANK, results=results),
        direction=direction,
        campaign=campaign,
        on_start=on_start,
    )


def _agent_lines(result: ScenarioResult) -> list[str]:
    return [t.text for t in result.transcript if t.role == "agent"]


def _intents(result: ScenarioResult) -> list[str]:
    return [str(i) for i in result.state.intents]


# --------------------------------------------------------------------------- the three routes


async def test_the_router_asks_how_it_can_help_in_code(prompts: PromptRegistry, stand_ins: None) -> None:
    result = await _run(prompts, [call("route_policy_question")], ["Can I pay early?"])
    assert _agent_lines(result)[0] == HOW_CAN_I_HELP == "How can I help?"
    assert result.state.states_visited[0] == "router"


async def test_a_policy_question_reaches_policy_with_no_verification(
    prompts: PromptRegistry, stand_ins: None
) -> None:
    result = await _run(prompts, [call("route_policy_question")], ["Can I pay early?"])
    assert result.states == ["router", "policy"]
    assert "verify_identity" not in result.gateway.tools_called
    assert result.state.verified is False
    assert _intents(result) == ["policy_question"]
    assert result.llm.remaining == 0


async def test_an_account_question_forces_verification_before_any_account_tool(
    prompts: PromptRegistry, stand_ins: None
) -> None:
    result = await _run(
        prompts,
        [call("route_account_question"), VERIFY_FRANK],
        ["What do I owe?", "Frank Alvarez, 2 January 1980, 4417"],
    )
    assert result.states == ["router", "verify", "account"]
    assert result.gateway.tools_called == [
        "verify_identity"
    ]  # no account tool before it, and none by the router
    assert result.state.verified is True
    assert _intents(result) == ["account_question"]
    # verification went to the account state the caller asked for, not back to the router
    assert result.state.after_verification is None
    assert result.llm.remaining == 0


async def test_a_request_for_a_person_goes_to_escalation_without_verification(
    prompts: PromptRegistry, stand_ins: None
) -> None:
    result = await _run(prompts, [call("request_human")], ["I want to talk to a real person."])
    assert result.states == ["router", "escalation"]
    assert _StandIn.entered[-1] == ("escalation", {"reason": "human_requested"})
    assert result.gateway.tools_called == []
    assert result.state.verified is False
    assert _intents(result) == ["human_request"]


# --------------------------------------------------------------------------- switching within one call


async def test_policy_then_account_then_account_again_verifies_only_once(
    prompts: PromptRegistry, stand_ins: None
) -> None:
    result = await _run(
        prompts,
        [
            call("route_policy_question"),  # "Can I pay early?"
            call("finish"),  # policy answered, back to the router
            call("route_account_question"),  # "What do I owe?" -> verify first
            VERIFY_FRANK,
            call("finish"),  # account answered, back to the router
            call("route_account_question"),  # "And my next due date?" -> already verified
        ],
        [
            "Can I pay early?",
            "No, that's all on that.",
            "What do I owe?",
            "Frank Alvarez, 2 January 1980, 4417",
            "Thanks.",
            "And when is my next payment due?",
        ],
    )
    assert result.states == ["router", "policy", "router", "verify", "account", "router", "account"]
    assert result.gateway.tools_called.count("verify_identity") == 1
    assert _intents(result) == ["policy_question", "account_question"]  # each recorded once, in order
    assert result.llm.remaining == 0


async def test_coming_back_to_the_router_asks_if_there_is_anything_else(
    prompts: PromptRegistry, stand_ins: None
) -> None:
    result = await _run(
        prompts,
        [call("route_policy_question"), call("finish"), call("request_human")],
        ["Can I pay early?", "Thanks.", "Actually, can I talk to someone?"],
    )
    lines = _agent_lines(result)
    assert lines.count(HOW_CAN_I_HELP) == 1
    assert ANYTHING_ELSE in lines
    assert lines.index(HOW_CAN_I_HELP) < lines.index(ANYTHING_ELSE)


async def test_an_already_verified_caller_goes_straight_to_account(
    prompts: PromptRegistry, stand_ins: None
) -> None:
    result = await _run(prompts, [call("route_account_question")], ["What do I owe?"], verified=True)
    assert result.states == ["router", "account"]
    assert "verify_identity" not in result.gateway.tools_called


# --------------------------------------------------------------------------- the inbound call end to end


async def test_an_inbound_call_goes_greeting_then_router_then_policy(
    prompts: PromptRegistry, stand_ins: None
) -> None:
    result = await _run(
        prompts,
        [call("route_policy_question")],
        ["Can I pay early?"],
        start=GreetingAgent(prompts=prompts, variables=VARS),
    )
    assert result.states == ["greeting", "router", "policy"]
    lines = _agent_lines(result)
    assert lines[0] == REGISTRY.render("ai_recording", **VARS).text
    assert lines[1] == HOW_CAN_I_HELP
    assert "verify_identity" not in result.gateway.tools_called


# --------------------------------------------------------------------------- code paths and safety


BOOKED = ToolResult.success(
    TaskCreatedOutput(task_id=7, type="callback", due_at=dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.UTC))
)
PROMISES = ("will call you back", "will be in touch", "will follow up", "colleague will")


async def test_a_successor_that_has_not_landed_keeps_the_call_alive(
    prompts: PromptRegistry, stand_ins: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(STATE_REGISTRY, "policy")
    result = await _run(
        prompts,
        [call("route_policy_question"), say("Someone will follow up on that.")],
        ["Can I pay early?"],
        results={"request_callback": BOOKED},
    )
    assert result.states == ["router"]
    assert _intents(result) == ["policy_question", "callback_request"]  # both recorded
    assert _agent_lines(result)[-1] != "Someone will follow up on that."  # the model never got to speak


async def test_an_unavailable_state_promises_a_callback_only_after_the_gateway_booked_it(
    prompts: PromptRegistry, stand_ins: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first real call: "A colleague will be in touch" was said with no callback ticket behind it."""
    monkeypatch.delitem(STATE_REGISTRY, "escalation")
    result = await _run(
        prompts,
        [call("request_human"), say("A colleague will be in touch with you shortly.")],
        ["I want to talk to a person"],
        results={"request_callback": BOOKED},
    )
    callbacks = [c for c in result.gateway.calls if c.tool == "request_callback"]
    assert len(callbacks) == 1
    assert result.state.outcome.callback is not None
    line = _agent_lines(result)[-1]
    assert (
        flow.CALLBACK_BOOKED_LINE.format(when="10 November 2026 at 09:00 UTC") == line
    )  # time from the result
    assert "colleague will be in touch" not in " ".join(
        _agent_lines(result)
    )  # the model's promise never spoken


async def test_an_unavailable_state_promises_nothing_when_the_callback_cannot_be_booked(
    prompts: PromptRegistry, stand_ins: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(STATE_REGISTRY, "escalation")
    refused = ToolResult.failure("not_verified", "refused")
    for results in ({}, {"request_callback": refused}):  # no fake result at all, and an explicit refusal
        result = await _run(
            prompts,
            [call("request_human"), say("A colleague will be in touch with you shortly.")],
            ["I want to talk to a person"],
            results=results,
        )
        assert result.state.outcome.callback is None
        line = _agent_lines(result)[-1]
        assert line == flow.CALLBACK_DECLINED_LINE
        assert not any(p in " ".join(_agent_lines(result)).lower() for p in PROMISES)


async def test_verification_for_an_account_question_falls_back_when_account_is_missing(
    prompts: PromptRegistry, stand_ins: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(STATE_REGISTRY, "account")
    result = await _run(
        prompts,
        [call("route_account_question"), VERIFY_FRANK, say("A colleague will follow up.")],
        ["What do I owe?", "Frank Alvarez, 2 January 1980, 4417"],
        results={"request_callback": BOOKED},
    )
    assert result.states == ["router", "verify"]
    assert result.state.verified is True
    assert result.state.after_verification is None
    # The model's "A colleague will follow up." never spoke: the promise is the booked callback and its time.
    lines = _agent_lines(result)
    assert "A colleague will follow up." not in lines
    assert lines[-1] == flow.CALLBACK_BOOKED_LINE.format(when="10 November 2026 at 09:00 UTC")


async def test_locked_verification_without_an_escalation_state_promises_nothing_unbooked(
    prompts: PromptRegistry, stand_ins: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(STATE_REGISTRY, "escalation")
    wrong = call("verify_identity", full_name="Frank Alvarez", dob="1999-09-09", loan_last4="4417")
    result = await _run(
        prompts,
        [call("route_account_question"), wrong, wrong],  # two failed attempts: the second one locks the call
        ["What do I owe?", "Frank Alvarez, 9 September 1999, 4417"],
    )  # no `request_callback` result: the gateway "fails"
    assert result.state.locked is True
    assert _agent_lines(result)[-1] == flow.CALLBACK_DECLINED_LINE
    assert not any(p in " ".join(_agent_lines(result)).lower() for p in PROMISES)


async def test_the_router_has_exactly_its_four_tools_and_no_data_tools() -> None:
    names = set(class_tool_names(RouterAgent))
    assert names == {
        "route_account_question",
        "route_policy_question",
        "request_human",
        "finish_call",
        "report_concern",
    }
    assert not names & {"get_loan_status", "get_recent_transactions", "search_policy", "verify_identity"}
    check_registered_states()


def test_the_router_is_registered_under_its_flow_name() -> None:
    assert flow.resolve(flow.ROUTER) is RouterAgent
    assert flow.ACCOUNT == "account"
    assert flow.POLICY == "policy"


def test_intents_are_recorded_once_each_in_order() -> None:
    state = CallState(
        metadata=CallMetadata(
            call_id="3f2a9c1e-5b7d-4e8a-9c0b-1d2e3f4a5b6c",
            direction=Direction.inbound,
            campaign=Campaign.inbound,
        ),
        gateway=FakeGateway(FRANK),
        session_token=SecretStr("fake-session-token"),
    )
    for intent in (Intent.policy_question, Intent.account_question, Intent.policy_question):
        state.record_intent(intent)
    assert state.intents == [Intent.policy_question, Intent.account_question]
    assert state.outcome_so_far()["intents"] == ["policy_question", "account_question"]


# ------------------------------------------------------------ ending the call (the first real call)


def _no_figures(lines: list[str]) -> bool:
    return not re.search(r"[$\d]", " ".join(lines))


async def test_a_goodbye_ends_the_call_in_code_without_a_model_reply(prompts: PromptRegistry) -> None:
    """The first real call: the caller said bye, the agent said "Goodbye!" and the call kept running."""
    result = await _run(prompts, [], ["Okay, sure. Bye."])
    assert result.states == ["router", "closing"]
    assert result.state.end_reason == flow.END_BY_CALLER
    lines = _agent_lines(result)
    assert lines == [HOW_CAN_I_HELP, goodbye_line("Meridian Bank")]
    assert _no_figures(lines)  # an unverified caller gets a plain goodbye, no account figures
    assert result.llm.remaining == 0


async def test_a_bare_no_after_anything_else_ends_the_call(prompts: PromptRegistry, stand_ins: None) -> None:
    result = await _run(
        prompts,
        [call("route_policy_question"), call("finish")],  # the policy stand-in's `finish` returns here
        ["Can I pay early?", "Okay, that makes sense.", "No thanks."],
    )
    assert result.states == ["router", "policy", "router", "closing"]
    assert _agent_lines(result)[-2:] == [ANYTHING_ELSE, goodbye_line("Meridian Bank")]
    assert result.state.end_reason == flow.END_BY_CALLER
    assert result.llm.remaining == 0


async def test_a_bare_no_is_not_a_goodbye_before_the_agent_has_asked_anything_else(
    prompts: PromptRegistry,
) -> None:
    result = await _run(prompts, [say("Which do you mean?")], ["No thanks."])
    assert result.states == ["router"]  # still on the call: "no" answered "how can I help?"
    assert result.state.end_reason is None


async def test_a_goodbye_with_a_question_does_not_end_the_call(prompts: PromptRegistry) -> None:
    result = await _run(
        prompts, [say("I can help with fees or your account.")], ["Goodbye, what am I doing here?"]
    )
    assert result.states == ["router"]
    assert result.state.end_reason is None


async def test_the_model_can_end_the_call_with_finish_call(prompts: PromptRegistry) -> None:
    result = await _run(prompts, [call("finish_call")], ["I think we are finished here then"])
    assert result.states == ["router", "closing"]
    assert result.state.end_reason == flow.END_BY_CALLER
    assert _agent_lines(result)[-1] == goodbye_line("Meridian Bank")


async def test_a_farewell_reads_back_a_commitment_before_the_goodbye(prompts: PromptRegistry) -> None:
    async def on_start(state: CallState) -> None:
        state.disclosures = REGISTRY
        state.verified = True
        state.outcome.record_callback(dt.datetime(2026, 11, 12, 9, 0, tzinfo=dt.UTC))

    result = await run_text_scenario(
        FRANK,
        ["That's all, goodbye."],
        agent=RouterAgent(prompts=prompts, variables=VARS),
        llm=ScriptedLLM([]),
        gateway=FakeGateway(FRANK),
        direction=Direction.inbound,
        campaign=Campaign.inbound,
        on_start=on_start,
    )
    lines = _agent_lines(result)
    assert lines[-1] == goodbye_line("Meridian Bank")
    assert "call you back" in lines[-2]  # the commitment the gateway scheduled, said before the goodbye
