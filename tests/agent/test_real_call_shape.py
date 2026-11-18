"""AGENT-R1 · the shape of the first real playground call, replayed in text mode with the real states.

On 2026-11-17 a caller asked a policy question, then "how much do I owe the bank", then said thanks and
goodbye. The agent answered the second question with the policy fallback, promised "a colleague will be in
touch" with no callback behind it, said "Goodbye!" and kept the call open. This replays that conversation
with synthetic data: the policy answer, the hand-off to verification, the account state, and a goodbye that
ends the call.
"""

import datetime as dt
import re
from pathlib import Path

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent.states import flow
from supportagent.agent.states.closing import goodbye_line
from supportagent.agent.states.router import ANYTHING_ELSE, HOW_CAN_I_HELP, RouterAgent
from supportagent.agent.states.verify import ASK_FOR_DETAILS
from supportagent.agent.testing import FakeGateway, ScenarioResult, ScriptedLLM, call, run_text_scenario, say
from supportagent.compliance.disclosures import load_disclosures
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.gateway import PolicySource, SearchPolicyOutput, ToolResult
from supportagent.contracts.outcome import Campaign, Direction

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_disclosures(ROOT / "data" / "config" / "disclosures.yaml")
VARS = {"bank_name": "Meridian Bank", "agent_name": "Ava"}
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
FEES = PolicySource(
    doc_id="fees-schedule",
    version="2.1",
    section="Personal loan fees",
    text="The late fee on a personal loan is a flat $25.",
    score=0.74,
)
PROMISES = ("will call you back", "will be in touch", "will follow up", "colleague will")


def _lines(result: ScenarioResult) -> list[str]:
    return [t.text for t in result.transcript if t.role == "agent"]


async def test_policy_then_account_then_goodbye_ends_the_call_with_no_unbooked_promise() -> None:
    async def on_start(state) -> None:  # type: ignore[no-untyped-def]
        state.disclosures = REGISTRY

    llm = ScriptedLLM(
        [
            # 1. the caller asks how late fees work
            call("route_policy_question"),
            call("search_policy", query="late fees on personal loans"),
            say("The late fee on a personal loan is a flat twenty-five dollars."),
            # 3. the caller gives their details: verification succeeds, the account state takes over
            call("verify_identity", full_name="Frank Alvarez", dob="1980-01-02", loan_last4="4417"),
            say("Thank you. What would you like to know about your loan?"),
            # 4. "that's all": the account state gives the call back to the router
            call("finish"),
        ]
    )
    result = await run_text_scenario(
        FRANK,
        [
            "What are the late fees on personal loans?",
            "And how much do I owe the bank?",  # 2. an account question asked in the policy state
            "Frank Alvarez, 2 January 1980, 4417",
            "That is everything, thank you.",
            "No thanks. Goodbye.",  # 5. after "anything else?": the call ends in code
        ],
        agent=RouterAgent(variables=VARS),
        llm=llm,
        gateway=FakeGateway(
            FRANK,
            results={
                "search_policy": ToolResult.success(SearchPolicyOutput(chunks=[FEES], low_confidence=False))
            },
        ),
        direction=Direction.inbound,
        campaign=Campaign.inbound,
        on_start=on_start,
    )

    assert result.states == ["router", "policy", "verify", "account", "router", "closing"]
    lines = _lines(result)
    assert lines[0] == HOW_CAN_I_HELP
    assert ASK_FOR_DETAILS in lines  # the second question went to verification, not the policy fallback
    assert not any("can't look that up" in line or "I'm not sure about that" in line for line in lines)
    assert ANYTHING_ELSE in lines
    assert lines[-1] == goodbye_line("Meridian Bank")
    assert result.state.end_reason == flow.END_BY_CALLER
    assert result.state.verified is True
    assert not any(p in " ".join(lines).lower() for p in PROMISES)  # nothing promised, nothing booked
    assert [c for c in result.gateway.calls if c.tool == "request_callback"] == []
    assert not re.search(r"\$\d+\.\d\d", lines[-1])  # the goodbye carries no figures
    assert llm.remaining == 0
