"""T-EVAL-01 · EVAL-R1: `run_scenario` is deterministic, offline, and holds the gateway's guardrails."""

import dataclasses
import re
import uuid
from pathlib import Path

import pytest

from supportagent.agent.base import STATE_REGISTRY
from supportagent.agent.prompts.registry import PromptRegistry
from supportagent.agent.testing import call, say
from supportagent.contracts.outcome import ContactResult, Direction
from supportagent.evals import harness
from supportagent.evals.fakes import FakeGateway, ScriptedLLM, follow_expected_tools
from supportagent.evals.harness import Scenario, run_scenario
from supportagent.evals.records import RunRecord
from tests.evals.desk import (
    AMOUNT,
    CLAIM,
    FRANK,
    SPOKEN_AMOUNT,
    desk_agent,
    frank_data,
    policy_question,
    verify_then_balance,
)

WRONG_DOB = {**CLAIM, "dob": "1985-03-15"}


def _agent(prompts: PromptRegistry):  # type: ignore[no-untyped-def]
    return lambda: desk_agent(prompts)


# -------------------- determinism


async def test_two_runs_give_identical_json(prompts: PromptRegistry) -> None:
    first = await run_scenario(verify_then_balance(), agent=_agent(prompts))
    second = await run_scenario(verify_then_balance(), agent=_agent(prompts))
    assert first.to_json() == second.to_json()
    assert RunRecord.model_validate_json(first.to_json()) == first


async def test_a_policy_scenario_is_deterministic_too(prompts: PromptRegistry) -> None:
    runs = [await run_scenario(policy_question(), agent=_agent(prompts)) for _ in range(2)]
    assert runs[0].to_json() == runs[1].to_json()


async def test_call_id_comes_from_the_scenario_id() -> None:
    a, b = harness.scenario_call_id("one"), harness.scenario_call_id("two")
    assert a == harness.scenario_call_id("one") != b
    assert uuid.UUID(a).version == 4


# -------------------- record contents


async def test_verify_then_balance_record(prompts: PromptRegistry) -> None:
    record = await run_scenario(verify_then_balance(), agent=_agent(prompts))

    assert record.mode == "scripted" and record.scenario_id == "inline-verify-then-balance"
    assert record.states == ["desk"]
    assert [(t.seq, t.speaker) for t in record.turns] == [(1, "caller"), (2, "agent")]
    assert [t.ts for t in record.turns] == sorted(t.ts for t in record.turns)
    assert all(t.call_id == record.call_id for t in record.turns)
    assert SPOKEN_AMOUNT in record.turns[1].text

    assert [(c.name, c.ok, c.decision) for c in record.tool_calls] == [
        ("verify_identity", True, "allow"),
        ("get_loan_status", True, "allow"),
    ]
    assert [m.name for m in record.model_calls] == ["verify", "get_loan_status"]
    assert [(a.tool, a.decision, a.customer_id) for a in record.audit] == [
        ("verify_identity", "allow", FRANK.customer_id),
        ("get_loan_status", "allow", FRANK.customer_id),
    ]
    assert all(len(a.args_hash) == 64 for a in record.audit)

    out = record.outcome
    assert (out.verified, out.contact_result, out.customer_id) == (True, ContactResult.right_party, 9002)
    assert out.direction is Direction.outbound and out.call_id == record.call_id
    assert out.versions == record.versions and record.versions.model == "scripted/scripted"
    assert record.llm_model == "scripted/scripted"
    assert record.bank_snapshot["promises"] == []


async def test_policy_question_needs_no_verification_and_records_its_source(prompts: PromptRegistry) -> None:
    record = await run_scenario(policy_question(), agent=_agent(prompts))
    assert [(c.name, c.decision) for c in record.tool_calls] == [("search_policy", "allow")]
    assert record.outcome.verified is False
    assert record.outcome.kb_sources == ["late-fees-penalty-interest@1.0#Late fees"]
    assert "$25" in record.turns[-1].text


async def test_a_recorded_promise_lands_in_the_snapshot(prompts: PromptRegistry) -> None:
    scenario = dataclasses.replace(
        verify_then_balance(),
        id="inline-promise",
        agent_script=[
            call("verify", **CLAIM),
            call("record_promise", promise_date="2026-11-12", amount=str(AMOUNT)),
            say("Thank you, I have noted your promise."),
        ],
    )
    record = await run_scenario(scenario, agent=_agent(prompts))
    assert [p["promised_date"] for p in record.bank_snapshot["promises"]] == ["2026-11-12"]
    assert record.outcome.promise_to_pay is not None
    assert record.outcome.promise_to_pay.amount == AMOUNT


# -------------------- guardrails hold whatever the LLM does


async def test_a_model_that_reads_the_account_first_is_denied(prompts: PromptRegistry) -> None:
    scenario = dataclasses.replace(
        verify_then_balance(),
        id="inline-misbehave",
        misbehave="call_get_loan_status_first",
        agent_script=[say("I need to verify who I am speaking with before I can share account details.")],
    )
    record = await run_scenario(scenario, agent=_agent(prompts))

    # The model tried to read the account first. The agent-side guard (T-AGENT-04, `VerifiedGateway`) answers
    # `not_verified` itself, so the request never reaches the gateway: no gateway call and no audit row.
    # The gateway's own deny of an unverified read is covered by test_gateway_conformance.py.
    assert record.model_calls[0].name == "get_loan_status"
    assert all(c.name != "get_loan_status" for c in record.tool_calls)
    assert all(a.tool != "get_loan_status" for a in record.audit)
    # No amount, date or account digits reached the caller.
    assert not re.search(r"[$\d]", record.agent_text), record.agent_text
    assert record.outcome.verified is False


def test_unknown_misbehave_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown misbehave mode"):
        ScriptedLLM([], misbehave="do_a_backflip")


async def test_two_wrong_dobs_lock_the_call_and_escalate(prompts: PromptRegistry) -> None:
    gateway = FakeGateway(
        frank_data(),
        persona=FRANK,
        direction=Direction.outbound,
        loan_id=FRANK.loan_id,
        call_id=harness.scenario_call_id("inline-lockout"),
    )
    scenario = dataclasses.replace(
        verify_then_balance(),
        id="inline-lockout",
        caller_turns=["March 15, 1985", "April 14, 1985", "OK it is March 14, 1985"],
        agent_script=[
            call("verify", **WRONG_DOB),
            say("That does not match. Could you say your date of birth again?"),
            call("verify", **{**WRONG_DOB, "dob": "1985-04-14"}),
            say("I'm sorry, I can't verify you. A colleague will follow up."),
            call("verify", **CLAIM),  # the correct claim, too late
            say("I'm sorry, I can't continue."),
        ],
    )
    record = await run_scenario(scenario, gateway=gateway, agent=_agent(prompts))

    verifies = [c for c in record.tool_calls if c.name == "verify_identity"]
    assert [(c.ok, c.error_code) for c in verifies] == [(True, None), (True, None), (False, "locked")]
    second = gateway.responses[1].data
    assert second is not None and (second.verified, second.locked, second.attempts_remaining) == (
        False,
        True,
        0,
    )

    out = record.outcome
    assert out.verified is False
    assert out.escalated is not None and out.escalated.reason == "verification_failed"
    assert out.escalated.target == "200"
    assert [t["reason"] for t in record.bank_snapshot["tasks"]] == ["verification_failed"]
    # The third attempt was refused although the claim was right: a locked call stays locked.
    assert record.audit[-1].decision == "deny"
    assert record.bank_snapshot["promises"] == []


# -------------------- the default policy and the entry state


async def test_default_policy_calls_the_expected_tool_then_replies(prompts: PromptRegistry) -> None:
    from supportagent.agent.testing import ToolCallSpec

    scenario = dataclasses.replace(
        verify_then_balance(),
        id="inline-default-policy",
        agent_script=None,
        expected_tools=[ToolCallSpec("verify", CLAIM), ToolCallSpec("get_loan_status")],
    )
    record = await run_scenario(scenario, agent=_agent(prompts))
    assert [c.name for c in record.tool_calls] == ["verify_identity", "get_loan_status"]
    assert record.outcome.verified is True


def test_policy_ignores_tools_the_state_does_not_offer() -> None:
    from livekit.agents.llm import ChatContext

    from supportagent.agent.testing import ToolCallSpec

    policy = follow_expected_tools([ToolCallSpec("verify", CLAIM)], reply="Hello.")
    assert policy(ChatContext(), ["ask_policy"]).text == "Hello."
    assert policy(ChatContext(), ["verify"]).calls[0].name == "verify"


async def test_a_run_without_an_entry_state_says_what_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    import supportagent.agent.states  # noqa: F401  (register the real states first, then remove the entry)

    monkeypatch.delitem(STATE_REGISTRY, harness.ENTRY_STATE)
    with pytest.raises(harness.NoEntryState, match="agent="):
        await run_scenario(verify_then_balance())


async def test_simulated_mode_is_not_available_yet(prompts: PromptRegistry) -> None:
    with pytest.raises(NotImplementedError, match="T-EVAL-04"):
        await run_scenario(verify_then_balance(), mode="simulated", agent=_agent(prompts))


def test_the_scenario_type_is_data_only() -> None:
    assert dataclasses.is_dataclass(Scenario) and Path(harness.__file__).name == "harness.py"


def test_unit_tests_cannot_open_a_connection() -> None:
    import socket

    from tests.evals.conftest import NetworkBlocked

    with pytest.raises(NetworkBlocked), socket.socket() as s:
        s.connect(("127.0.0.1", 9))
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("example.com", 80)
