"""A synthetic `desk` state and two inline scenarios for the harness tests.

The real conversation states arrive with T-AGENT-04..08. `DeskAgent` is a small stand-in built on the
T-AGENT-02 framework: four tools that wrap the gateway, with the guard logic the real states will have
(a locked call escalates in code, a denied read never turns into a guess).
"""

import datetime as dt
from decimal import Decimal

from livekit.agents import RunContext, function_tool

from supportagent.agent.base import BaseStateAgent
from supportagent.agent.context import CallState
from supportagent.agent.prompts.registry import PromptRegistry
from supportagent.agent.testing import call, say
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.gateway import (
    EscalationReason,
    PolicySource,
    SearchPolicyInput,
    SearchPolicyOutput,
)
from supportagent.contracts.outcome import Campaign, Direction
from supportagent.evals.fakes import FakeBankData
from supportagent.evals.harness import Scenario

AS_OF = dt.date(2026, 11, 9)
FRANK = PersonaCard(
    customer_id=9002,
    full_name="Frank Alvarez",
    phone="+15550100302",
    extension="1002",
    dob=dt.date(1985, 3, 14),
    loan_id=9002,
    loan_last4="3310",
    product_code="PERSONAL",
    situation="synthetic: forgot to pay",
    expected_outcome="promise to pay",
)
DUE_DATE = AS_OF - dt.timedelta(days=5)
AMOUNT = Decimal("412.50")
SPOKEN_AMOUNT = "$412.50"


class DeskAgent(BaseStateAgent):
    state_name = "desk"

    @function_tool
    async def verify(self, context: RunContext[CallState], full_name: str, dob: str, loan_last4: str) -> str:
        """Check who the caller is.

        Args:
            full_name: The caller's full name.
            dob: Date of birth as YYYY-MM-DD.
            loan_last4: The last four digits of the loan number.
        """
        state = context.userdata
        result = await state.gateway.verify_identity(full_name, dt.date.fromisoformat(dob), loan_last4)
        if not result.ok or result.data is None:
            state.locked = state.locked or (result.error is not None and result.error.code == "locked")
            return f"cannot verify: {result.error.code if result.error else 'error'}"
        if result.data.verified:
            state.verified, state.first_name = True, result.data.first_name
            return "verified"
        if result.data.locked:
            state.locked = True
            await state.gateway.escalate(EscalationReason.verification_failed, "caller could not be verified")
            return "locked: a colleague will follow up"
        return f"not verified, {result.data.attempts_remaining} attempt left"

    @function_tool
    async def get_loan_status(self, context: RunContext[CallState]) -> str:
        """Read the verified caller's loan status."""
        result = await context.userdata.gateway.get_loan_status()
        if not result.ok or result.data is None:
            return f"not available: {result.error.code if result.error else 'error'}"
        return "; ".join(loan.speakable for loan in result.data.loans)

    @function_tool
    async def ask_policy(self, context: RunContext[CallState], query: str) -> str:
        """Look up a bank policy.

        Args:
            query: What the caller asked about.
        """
        result = await context.userdata.gateway.search_policy(query)
        if not result.ok or result.data is None or result.data.low_confidence:
            return "no confident answer: offer a follow-up"
        return result.data.chunks[0].text

    @function_tool
    async def record_promise(self, context: RunContext[CallState], promise_date: str, amount: str) -> str:
        """Record a promise to pay.

        Args:
            promise_date: YYYY-MM-DD.
            amount: The amount in dollars.
        """
        result = await context.userdata.gateway.record_promise_to_pay(
            dt.date.fromisoformat(promise_date), Decimal(amount)
        )
        return result.data.reason if result.ok and result.data else "failed"


def desk_agent(prompts: PromptRegistry) -> DeskAgent:
    return DeskAgent(prompts=prompts, variables={"agent_name": "Ava", "bank_name": "Meridian Bank"})


def frank_data() -> FakeBankData:
    return FakeBankData.for_persona(FRANK, as_of=AS_OF, overdue_amount=AMOUNT, days_past_due=5)


CLAIM = {"full_name": "Frank Alvarez", "dob": "1985-03-14", "loan_last4": "3310"}


def verify_then_balance() -> Scenario:
    return Scenario(
        id="inline-verify-then-balance",
        persona=FRANK,
        data=frank_data,
        direction=Direction.outbound,
        campaign=Campaign.missed_payment,
        loan_id=FRANK.loan_id,
        caller_turns=["Yes, this is Frank. March 14, 1985, loan ending 3310. What do I owe?"],
        agent_script=[
            call("verify", **CLAIM),
            call("get_loan_status"),
            say(f"Thanks Frank. You have {SPOKEN_AMOUNT} overdue since 4 November 2026."),
        ],
    )


def policy_question() -> Scenario:
    chunk = PolicySource(
        doc_id="late-fees-penalty-interest",
        version="1.0",
        section="Late fees",
        text="A personal loan late fee is a flat $25 after the grace period.",
        score=0.71,
    )

    def policy(inp: SearchPolicyInput) -> SearchPolicyOutput:
        return SearchPolicyOutput(chunks=[chunk], low_confidence=False)

    return Scenario(
        id="inline-policy-question",
        persona=FRANK,
        data=frank_data,
        direction=Direction.inbound,
        campaign=Campaign.inbound,
        caller_turns=["What is the late fee on a personal loan?"],
        policy=policy,
        agent_script=[
            call("ask_policy", query="late fee personal loan"),
            say("A personal loan late fee is a flat $25 after the grace period."),
        ],
    )
