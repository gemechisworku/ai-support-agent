"""T-AGENT-04 · AGENT-R2 / AGENT-R4: greeting, disclosure and identity verification in text mode.

Successor states (router, reminder, escalation, ...) belong to later tasks, so the tests register stand-ins
under the names the flow table uses and check the hand-offs by name.
"""

import datetime as dt
import re
import shutil
from pathlib import Path
from typing import ClassVar

import pytest

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent import states as states_pkg
from supportagent.agent.base import STATE_REGISTRY, BaseStateAgent, check_registered_states
from supportagent.agent.context import CallState
from supportagent.agent.guard import GUARDED_TOOLS, VerifiedGateway
from supportagent.agent.prompts.registry import DEFAULT_PROMPTS_DIR, PromptRegistry
from supportagent.agent.states import flow
from supportagent.agent.states.greeting import GreetingAgent
from supportagent.agent.states.third_party import ThirdPartyAgent
from supportagent.agent.states.verify import VerifyAgent
from supportagent.agent.testing import (
    FakeGateway,
    ScenarioResult,
    ScriptedLLM,
    call,
    run_text_scenario,
    say,
)
from supportagent.compliance.disclosures import ACCOUNT_DATA_TOOLS, load_disclosures
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.gateway import LookupByPhoneOutput, ToolResult
from supportagent.contracts.outcome import Campaign, ContactResult, Direction

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_disclosures(ROOT / "data" / "config" / "disclosures.yaml")
VARS = {"bank_name": "Meridian Bank", "agent_name": "Ava"}
AI_TEXT = REGISTRY.render("ai_recording", **VARS).text
AI_TAG = REGISTRY.tag("ai_recording")

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
LOOKUP_FRANK = ToolResult.success(LookupByPhoneOutput(match=True, first_name="Frank"))
ASK_DETAILS = "full name, your date of birth and the last four digits of your loan number"


class _StandIn(BaseStateAgent):
    """A later task's state: records that it was entered, and with which keyword arguments."""

    entered: ClassVar[list[tuple[str, dict[str, object]]]] = []

    def __init__(self, **kwargs: object) -> None:
        extra = {k: v for k, v in kwargs.items() if k not in {"prompts", "variables", "chat_ctx", "tools"}}
        base = {k: v for k, v in kwargs.items() if k not in extra}
        super().__init__(**base)  # type: ignore[arg-type]
        type(self).entered.append((self.state_name, extra))


def _stand_in(name: str) -> type[_StandIn]:
    return type(f"StandIn_{name}", (_StandIn,), {"state_name": name, "entered": []})


@pytest.fixture
def prompts(tmp_path: Path) -> PromptRegistry:
    """The real prompts plus a placeholder prompt for each stand-in state."""
    d = tmp_path / "prompts"
    shutil.copytree(DEFAULT_PROMPTS_DIR, d)
    for name in ("router", "reminder", "collections_purpose", "callback", "escalation"):
        (d / f"{name}.md").write_text("---\nversion: '1'\n---\nA later task's state.\n")
    return PromptRegistry(d)


@pytest.fixture
def stand_ins(monkeypatch: pytest.MonkeyPatch) -> dict[str, type[_StandIn]]:
    made = {n: _stand_in(n) for n in ("router", "reminder", "collections_purpose", "callback", "escalation")}
    for name, cls in made.items():
        monkeypatch.setitem(STATE_REGISTRY, name, cls)
    return made


async def _start(state: CallState) -> None:
    state.disclosures = REGISTRY


async def _run(
    prompts: PromptRegistry,
    steps: list,
    turns: list[str],
    *,
    direction: Direction = Direction.outbound,
    campaign: Campaign = Campaign.missed_payment,
    gateway: FakeGateway | None = None,
) -> ScenarioResult:
    gw = gateway or FakeGateway(FRANK, {"lookup_by_phone": LOOKUP_FRANK})
    return await run_text_scenario(
        FRANK,
        turns,
        agent=GreetingAgent(prompts=prompts, variables=VARS),
        llm=ScriptedLLM(steps),
        gateway=gw,
        direction=direction,
        campaign=campaign,
        on_start=_start,
    )


def _agent_lines(result: ScenarioResult) -> list[str]:
    return [t.text for t in result.transcript if t.role == "agent"]


# --------------------------------------------------------------------------- greeting


async def test_the_first_utterance_is_the_registry_text_and_is_recorded(
    prompts: PromptRegistry, stand_ins: dict[str, type[_StandIn]]
) -> None:
    result = await _run(prompts, [call("confirm_right_party")], ["Yes, this is Frank."])
    assert _agent_lines(result)[0] == AI_TEXT
    assert result.state.disclosures_given == [AI_TAG]
    assert AI_TAG == "ai_recording@1.0"


async def test_outbound_asks_for_the_first_name_from_the_phone_lookup(
    prompts: PromptRegistry, stand_ins: dict[str, type[_StandIn]]
) -> None:
    result = await _run(prompts, [call("confirm_right_party")], ["Yes, speaking."])
    assert _agent_lines(result)[:2] == [AI_TEXT, "May I speak with Frank?"]
    assert result.gateway.tools_called[0] == "lookup_by_phone"


async def test_outbound_without_a_lookup_match_asks_neutrally(
    prompts: PromptRegistry, stand_ins: dict[str, type[_StandIn]]
) -> None:
    gw = FakeGateway(FRANK, {"lookup_by_phone": ToolResult.success(LookupByPhoneOutput(match=False))})
    result = await _run(prompts, [call("confirm_right_party")], ["Yes."], gateway=gw)
    assert _agent_lines(result)[1] == "May I speak with the account holder?"
    assert "Frank" not in " ".join(_agent_lines(result))


async def test_inbound_never_speaks_a_name_before_verification(
    prompts: PromptRegistry, stand_ins: dict[str, type[_StandIn]]
) -> None:
    result = await _run(
        prompts,
        [say("How can I help you today?")],
        ["Hi, I have a question."],
        direction=Direction.inbound,
        campaign=Campaign.inbound,
        gateway=FakeGateway(FRANK),
    )
    assert _agent_lines(result)[0] == AI_TEXT
    assert "lookup_by_phone" not in result.gateway.tools_called
    assert result.states == ["greeting", "router"]
    assert not any("Frank" in line for line in _agent_lines(result))


async def test_wrong_number_records_the_result_and_says_nothing_about_the_account(
    prompts: PromptRegistry, stand_ins: dict[str, type[_StandIn]]
) -> None:
    result = await _run(
        prompts, [call("wrong_number"), say("Sorry about that, goodbye.")], ["No, wrong number."]
    )
    assert result.state.contact_result is ContactResult.wrong_number
    assert result.outcome["contact_result"] == "wrong_number"
    assert result.state.verified is False
    assert result.gateway.tools_called == ["lookup_by_phone"]
    assert result.states == ["greeting"]


# --------------------------------------------------------------------------- verification


async def test_frank_verifies_and_moves_to_his_campaign_state(
    prompts: PromptRegistry, stand_ins: dict[str, type[_StandIn]]
) -> None:
    result = await _run(
        prompts,
        [
            call("confirm_right_party"),
            call("verify_identity", full_name="Frank Alvarez", dob="1980-01-02", loan_last4="4417"),
        ],
        ["Yes, this is Frank.", "Frank Alvarez, January 2nd 1980, 4417"],
    )
    assert result.states == ["greeting", "verify", "collections_purpose"]
    assert ASK_DETAILS in _agent_lines(result)[2]
    assert "Frank" not in _agent_lines(result)[2]
    assert result.state.verified is True
    assert result.state.contact_result is ContactResult.right_party
    assert result.state.first_name == "Frank"
    assert result.outcome["verified"] is True
    assert result.gateway.tools_called == ["lookup_by_phone", "verify_identity"]
    assert result.llm.remaining == 0


@pytest.mark.parametrize(
    ("direction", "campaign", "expected"),
    [
        (Direction.outbound, Campaign.pre_due, "reminder"),
        (Direction.outbound, Campaign.missed_payment, "collections_purpose"),
        (Direction.outbound, Campaign.callback, "callback"),
        (Direction.inbound, Campaign.inbound, "router"),
        (Direction.inbound, Campaign.playground, "router"),
    ],
)
def test_the_state_after_verification_depends_on_direction_and_campaign(
    direction: Direction, campaign: Campaign, expected: str
) -> None:
    assert flow.post_verification_state(direction, campaign) == expected


async def test_two_wrong_birthdates_lock_the_call_and_escalate(
    prompts: PromptRegistry, stand_ins: dict[str, type[_StandIn]]
) -> None:
    result = await _run(
        prompts,
        [
            call("confirm_right_party"),
            call("verify_identity", full_name="Frank Alvarez", dob="1981-01-02", loan_last4="4417"),
            say("That did not match. Let's try once more."),
            call("verify_identity", full_name="Frank Alvarez", dob="1982-03-04", loan_last4="4417"),
        ],
        ["Yes.", "Frank Alvarez, 2 Jan 1981, 4417", "Frank Alvarez, 4 March 1982, 4417"],
    )
    assert result.states == ["greeting", "verify", "escalation"]
    assert stand_ins["escalation"].entered == [("escalation", {"reason": "verification_failed"})]
    assert result.state.locked is True
    assert result.state.verified is False
    assert result.outcome["verified"] is False
    # the first failure tells the model what to say without revealing which detail was wrong
    first = result.tool_calls[1].output or ""
    assert "1 attempt" in first
    assert "date of birth" not in first.lower()
    assert result.gateway.tools_called.count("verify_identity") == 2


async def test_a_malformed_date_or_last4_does_not_use_up_an_attempt(
    prompts: PromptRegistry, stand_ins: dict[str, type[_StandIn]]
) -> None:
    result = await _run(
        prompts,
        [
            call("confirm_right_party"),
            call("verify_identity", full_name="Frank Alvarez", dob="sometime in january", loan_last4="4417"),
            say("Sorry, what was your date of birth?"),
            call("verify_identity", full_name="Frank Alvarez", dob="1980-01-02", loan_last4="44"),
            say("And the last four digits of the loan number?"),
        ],
        ["Yes.", "Frank Alvarez, sometime in January, 4417", "Frank Alvarez, 2 Jan 1980, 44"],
    )
    assert "verify_identity" not in result.gateway.tools_called
    assert result.gateway.failed_verifications == 0
    assert result.states == ["greeting", "verify"]


async def test_a_gateway_error_during_verification_does_not_verify(
    prompts: PromptRegistry, stand_ins: dict[str, type[_StandIn]]
) -> None:
    class Down(FakeGateway):
        async def verify_identity(self, full_name, dob, loan_last4):  # type: ignore[no-untyped-def]
            return ToolResult.failure("upstream_error", "gateway unavailable")

    result = await _run(
        prompts,
        [
            call("confirm_right_party"),
            call("verify_identity", full_name="Frank Alvarez", dob="1980-01-02", loan_last4="4417"),
            say("I'm having trouble checking that right now."),
        ],
        ["Yes.", "Frank Alvarez, 2 Jan 1980, 4417"],
        gateway=Down(FRANK, {"lookup_by_phone": LOOKUP_FRANK}),
    )
    assert result.state.verified is False
    assert result.states == ["greeting", "verify"]


async def test_verification_without_a_successor_state_does_not_crash_the_call(
    prompts: PromptRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Until the campaign states exist (T-AGENT-05/09/10/12) a verified caller is told to expect follow-up."""
    for name in ("router", "reminder", "collections_purpose", "callback", "escalation"):
        monkeypatch.delitem(STATE_REGISTRY, name, raising=False)
    result = await _run(
        prompts,
        [
            call("confirm_right_party"),
            call("verify_identity", full_name="Frank Alvarez", dob="1980-01-02", loan_last4="4417"),
            say("Thank you, you're verified."),
        ],
        ["Yes.", "Frank Alvarez, 2 Jan 1980, 4417"],
    )
    assert result.state.verified is True
    assert result.states == ["greeting", "verify"]


# --------------------------------------------------------------------------- third party


async def test_the_spouse_gets_the_neutral_message_and_no_details(
    prompts: PromptRegistry, stand_ins: dict[str, type[_StandIn]]
) -> None:
    result = await _run(
        prompts,
        [
            call("third_party_answered"),
            call("record_callback_time", note="after six tomorrow evening"),
            call("end_call"),
            say("Thank you, goodbye."),
        ],
        ["This is his wife, he's out.", "After six tomorrow would be fine."],
    )
    neutral = REGISTRY.render("third_party_neutral", first_name="Frank", **VARS).text
    assert result.states == ["greeting", "third_party"]
    assert result.state.contact_result is ContactResult.third_party
    assert result.state.verified is False
    assert result.state.third_party_callback_note == "after six tomorrow evening"
    assert neutral in _agent_lines(result)
    assert REGISTRY.tag("third_party_neutral") in result.state.disclosures_given
    assert AI_TAG in result.state.disclosures_given
    spoken = " ".join(_agent_lines(result))
    assert not re.search(r"\$|\d", spoken.replace(AI_TEXT, "")), spoken
    for word in ("loan", "payment", "overdue", "balance", "personal", "owe", "amount", "due"):
        assert word not in spoken.lower().replace("convenience", ""), word
    # the spouse's turns reach no account tool at all
    assert not set(result.gateway.tools_called) & ACCOUNT_DATA_TOOLS
    assert result.gateway.tools_called == ["lookup_by_phone"]


# --------------------------------------------------------------------------- guardrails in code


async def test_account_data_is_unreachable_before_verification() -> None:
    gw = FakeGateway(FRANK)
    guard = VerifiedGateway(gw, lambda: False)
    for tool, args in {
        "get_loan_status": (),
        "get_recent_transactions": (),
        "get_interaction_history": (),
    }.items():
        result = await getattr(guard, tool)(*args)
        assert not result.ok and result.error is not None and result.error.code == "not_verified"
    result = await guard.record_promise_to_pay(dt.date(2026, 11, 12), 100)  # type: ignore[arg-type]
    assert result.error is not None and result.error.code == "not_verified"
    assert gw.calls == []  # blocked in code: the gateway was never reached
    assert ACCOUNT_DATA_TOOLS <= GUARDED_TOOLS


async def test_guard_passes_everything_once_verified_and_never_blocks_the_pre_verification_tools() -> None:
    gw = FakeGateway(FRANK, {"get_loan_status": ToolResult.failure("not_found", "x")})
    verified = {"yes": False}
    guard = VerifiedGateway(gw, lambda: verified["yes"])
    await guard.lookup_by_phone()
    await guard.search_policy("late fee")
    await guard.verify_identity("Frank Alvarez", dt.date(1980, 1, 2), "4417")
    assert gw.tools_called == ["lookup_by_phone", "search_policy", "verify_identity"]
    verified["yes"] = True
    result = await guard.get_loan_status()
    assert result.error is not None and result.error.code == "not_found"  # reached the gateway
    assert gw.tools_called[-1] == "get_loan_status"


async def test_call_state_guards_its_gateway_by_its_own_verified_flag() -> None:
    from pydantic import SecretStr

    from supportagent.contracts.call import CallMetadata

    gw = FakeGateway(FRANK)
    state = CallState(
        metadata=CallMetadata(
            call_id="7a8f3c3e-0000-4000-8000-000000000001",
            direction=Direction.inbound,
            campaign=Campaign.inbound,
        ),
        gateway=gw,
        session_token=SecretStr("t"),
    )
    blocked = await state.gateway.get_loan_status()
    assert blocked.error is not None and blocked.error.code == "not_verified"
    assert gw.calls == []
    state.verified = True
    await state.gateway.get_loan_status()
    assert gw.tools_called == ["get_loan_status"]


async def test_a_loan_status_request_before_verification_never_reaches_the_gateway(
    prompts: PromptRegistry, stand_ins: dict[str, type[_StandIn]]
) -> None:
    """Even if a state's model asked for account data early, the call state's guard refuses it."""
    gw = FakeGateway(FRANK)
    result = await _run(
        prompts, [call("confirm_right_party")], ["Yes."], direction=Direction.outbound, gateway=gw
    )
    blocked = await result.state.gateway.get_loan_status()
    assert blocked.error is not None and blocked.error.code == "not_verified"
    assert "get_loan_status" not in gw.tools_called


def test_the_new_states_keep_to_the_allow_list_and_are_registered() -> None:
    check_registered_states()
    for cls, tools in {
        GreetingAgent: {"confirm_right_party", "third_party_answered", "wrong_number"},
        VerifyAgent: {"verify_identity"},
        ThirdPartyAgent: {"record_callback_time", "end_call"},
    }.items():
        assert STATE_REGISTRY[cls.state_name] is cls
        from supportagent.agent.base import SHARED_TOOLS, class_tool_names

        assert set(class_tool_names(cls)) - SHARED_TOOLS == tools
    assert states_pkg.greeting.GreetingAgent is GreetingAgent
