"""Test doubles for the eval harness: an in-memory gateway with the real scope rules, and a scripted model.

`FakeGateway` is a `GatewayPort` (the agent's only door to data) backed by `FakeBankData` instead of the
bank. It re-implements the gateway's semantics so a scenario can run with no network and no database:

- **scope:** `unverified` tools stop working once the call is locked, `verified` tools need a bound
  customer (`not_verified`), `any` tools (policy, callback, escalate) always work, `worker` tools are the
  outcome write. A closed call (after `record_call_outcome`) refuses everything (`session_closed`).
- **identity:** the claim is checked against the expected customer (outbound) or the caller's phone /
  DOB + last 4 (inbound); name tokens within one edit, DOB equal, last 4 on one of the customer's loans.
  Exactly one match binds the customer; the second failure locks the call. No tool takes a customer id.
- **shaping:** outputs are built field by field, numbers masked to the last 4 digits, amounts and dates
  rendered from `Decimal` / `date` for the `speakable` strings.
- **business rules:** a promise must fall in today..today+14 days with 0 < amount <= amount due; a callback
  within 30 days; contact flags update the customer's preferences.
- **audit:** one `AuditRow` per request, allowed or denied, with the same salted `args_hash` as the real
  gateway (same canonical JSON of the same arguments).

Not modelled: rate limits, session expiry, the bank's HTTP errors, and the KB itself (`policy` is a function
you give it). `tests/evals/test_gateway_conformance.py` runs the same cases against this class and the real
gateway, so the two cannot drift apart unnoticed.

`ScriptedLLM` extends the T-AGENT-02 double with a default policy and `misbehave` modes: a model that does
the wrong thing on purpose, to show the guardrails are code and hold whatever the model does.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from livekit.agents.llm import ChatContext, Tool
from livekit.agents.llm.tool_context import get_fnc_tool_names
from pydantic import BaseModel, ValidationError

from supportagent.agent import testing as base
from supportagent.common import clock
from supportagent.contracts import bank as c
from supportagent.contracts.bank import PersonaCard, ProductCode, TimeWindow
from supportagent.contracts.gateway import (
    CallSummary,
    ContactFlag,
    ContactFlagOutput,
    EscalateInput,
    EscalateOutput,
    EscalationReason,
    GetLoanStatusInput,
    GetTransactionsInput,
    InteractionHistoryInput,
    InteractionHistoryOutput,
    LoanStatusOutput,
    LoanSummary,
    LookupByPhoneInput,
    LookupByPhoneOutput,
    OutcomeRecordedOutput,
    PromiseOut,
    RecordPromiseInput,
    RecordPromiseOutput,
    RequestCallbackInput,
    SearchPolicyInput,
    SearchPolicyOutput,
    SetContactFlagInput,
    TaskCreatedOutput,
    TaskOut,
    ToolErrorCode,
    ToolResult,
    TransactionOut,
    TransactionsOutput,
    VerifyIdentityInput,
    VerifyIdentityOutput,
)
from supportagent.contracts.outcome import (
    Callback,
    CallOutcome,
    Campaign,
    ContactResult,
    Direction,
    Escalation,
    PromiseToPay,
    Versions,
)

Scope = Literal["unverified", "verified", "any", "worker"]

MAX_ATTEMPTS = 2
MAX_TRANSACTIONS = 5
HISTORY_CALLS = 5
MAX_CALLBACK_AHEAD = dt.timedelta(days=30)
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
DEFAULT_SALT = "eval-audit-salt"
_LONG_DIGITS = re.compile(r"\d{5,}")

# --------------------------------------------------------------------------- the fake bank's data


@dataclass
class FakeBankData:
    """What the fake gateway can see and change: the bank's records for a few customers."""

    as_of: dt.date
    customers: dict[int, c.Customer] = field(default_factory=dict)
    loans: dict[int, c.Loan] = field(default_factory=dict)
    statuses: dict[int, c.LoanStatus] = field(default_factory=dict)
    transactions: list[c.Transaction] = field(default_factory=list)
    interactions: list[c.Interaction] = field(default_factory=list)
    promises: list[c.Promise] = field(default_factory=list)
    tasks: list[c.Task] = field(default_factory=list)
    prefs: dict[int, c.ContactPreferences] = field(default_factory=dict)
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1), repr=False)

    def next_id(self) -> int:
        return next(self._ids)

    @classmethod
    def for_persona(
        cls,
        persona: PersonaCard,
        *,
        as_of: dt.date,
        overdue_amount: Decimal = Decimal("412.50"),
        days_past_due: int = 5,
        next_amount: Decimal | None = None,
    ) -> FakeBankData:
        """One customer with one loan, `days_past_due` days late by `overdue_amount` (synthetic figures)."""
        due = as_of - dt.timedelta(days=days_past_due)
        late = days_past_due > 0
        instalment = next_amount if next_amount is not None else overdue_amount
        customer = c.Customer(
            id=persona.customer_id,
            full_name=persona.full_name,
            dob=persona.dob,
            phone=persona.phone,
            email="persona@example.test",
            address="1 Synthetic Way, Testville",
            timezone="America/New_York",
            segment="retail",
            kyc_status="verified",
        )
        loan = c.Loan(
            id=persona.loan_id,
            customer_id=persona.customer_id,
            product_code=persona.product_code,
            number=f"55202600{persona.loan_last4}",
            principal=Decimal("5000.00"),
            apr=Decimal("12.50"),
            term_months=24,
            start_date=as_of - dt.timedelta(days=200),
            status="active",
            days_past_due=days_past_due,
            bucket="1-30" if late else "current",
            repayment_account_id=persona.customer_id * 10,
        )
        status = c.LoanStatus(
            loan_id=loan.id,
            customer_id=customer.id,
            number_last4=persona.loan_last4,
            product_code=persona.product_code,
            product_name="Personal Loan",
            as_of=as_of,
            outstanding_balance=Decimal("3800.00"),
            overdue_amount=overdue_amount if late else ZERO,
            oldest_unpaid_due_date=due if late else None,
            next_due_date=as_of + dt.timedelta(days=25),
            next_due_amount=instalment,
            days_past_due=days_past_due,
            bucket=loan.bucket,
            fees_accrued=Decimal("25.00") if late else ZERO,
            status="active",
        )
        tx = c.Transaction(
            id=1,
            account_id=loan.repayment_account_id,
            loan_id=loan.id,
            ts=dt.datetime.combine(as_of - dt.timedelta(days=35), dt.time(10), tzinfo=dt.UTC),
            amount=instalment,
            channel="mobile",
            status="posted",
            reference=f"REF{persona.customer_id}0042",
        )
        prefs = c.ContactPreferences(
            customer_id=customer.id,
            consent_voice_ai=True,
            dnc=False,
            preferred_window=None,
            cease_contact=False,
            dispute_open=False,
            updated_at=dt.datetime.combine(as_of, dt.time(0), tzinfo=dt.UTC),
        )
        return cls(
            as_of=as_of,
            customers={customer.id: customer},
            loans={loan.id: loan},
            statuses={loan.id: status},
            transactions=[tx],
            prefs={customer.id: prefs},
        )

    def snapshot(self) -> dict[str, Any]:
        """What the call changed or left behind, as plain JSON (the `RunRecord.bank_snapshot`)."""
        dump: Callable[[BaseModel], dict[str, Any]] = lambda m: m.model_dump(mode="json")  # noqa: E731
        return {
            "promises": [dump(p) for p in self.promises],
            "tasks": [dump(t) for t in self.tasks],
            "contact_preferences": {str(k): dump(v) for k, v in sorted(self.prefs.items())},
            "interactions": [dump(i) for i in self.interactions],
        }


# --------------------------------------------------------------------------- shaping helpers


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def speakable_money(value: Decimal) -> str:
    return f"${money(value):,.2f}"


def speakable_date(value: dt.date) -> str:
    return f"{value.day} {value:%B %Y}"


def mask_reference(reference: str) -> str:
    return _LONG_DIGITS.sub(lambda m: f"****{m.group(0)[-4:]}", reference)


def normalise_name(name: str) -> list[str]:
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^\w\s]", "", ascii_only.casefold()).split()


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def names_match(spoken: str, registered: str, tolerance: int = 1) -> bool:
    said, known = normalise_name(spoken), normalise_name(registered)
    if len(said) < 2 or len(known) < 2:
        return False
    return levenshtein(said[0], known[0]) <= tolerance and levenshtein(said[-1], known[-1]) <= tolerance


def args_hash(salt: str, args: Any) -> str:
    canonical = json.dumps(
        args if args is not None else {}, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256((salt + canonical).encode()).hexdigest()


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['type']}" for e in exc.errors())


class _Denied(Exception):
    def __init__(self, code: ToolErrorCode, message: str = "") -> None:
        super().__init__(message or code)
        self.code: ToolErrorCode = code
        self.message = message


# --------------------------------------------------------------------------- the fake gateway


@dataclass(frozen=True)
class GatewayRules:
    """The gateway's configuration (defaults equal `GatewayConfig`'s)."""

    promise_max_days: int = 14
    allow_partial: bool = True
    human_agent_available: bool = True
    transfer_extension: str = "200"


@dataclass
class _Session:
    call_id: str
    direction: Direction
    campaign: Campaign | None
    loan_id: int | None
    caller_phone: str | None
    created_at: dt.datetime
    expected_customer_id: int | None = None
    candidate_ids: list[int] = field(default_factory=list)
    bound_customer_id: int | None = None
    failed_attempts: int = 0
    locked: bool = False
    closed: bool = False
    kb_sources: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.bound_customer_id is not None

    def record_source(self, citation: str) -> None:
        if citation not in self.kb_sources:
            self.kb_sources.append(citation)


PolicyFn = Callable[[SearchPolicyInput], SearchPolicyOutput]


class FakeGateway(base.FakeGateway):
    """A `GatewayPort` with the real scope rules over `FakeBankData`; records `audit` and `calls`.

    Build one per call. `direction`, `campaign`, `loan_id` and `caller_phone` are what the worker would send
    to `POST /sessions`: an outbound call knows the loan it is about, an inbound call may know the caller's
    phone. `audit` holds one `AuditRow` per request in order (ids from 1, timestamps from the clock).
    """

    def __init__(
        self,
        data: FakeBankData,
        *,
        persona: PersonaCard | None = None,
        direction: Direction = Direction.inbound,
        campaign: Campaign | None = Campaign.playground,
        loan_id: int | None = None,
        caller_phone: str | None = None,
        call_id: str = "00000000-0000-4000-8000-000000000001",
        rules: GatewayRules | None = None,
        policy: PolicyFn | None = None,
        salt: str = DEFAULT_SALT,
    ) -> None:
        super().__init__(persona)
        self.data = data
        self.rules = rules or GatewayRules()
        self.policy = policy
        self.salt = salt
        self.audit: list[c.AuditRow] = []
        self.responses: list[ToolResult[Any]] = []  # one per request, parallel to `calls` and `audit`
        self.escalation: Escalation | None = None
        self.callback: Callback | None = None
        self.flags: set[str] = set()
        self.session = _Session(
            call_id=call_id,
            direction=direction,
            campaign=campaign,
            loan_id=loan_id,
            caller_phone=caller_phone,
            created_at=clock.now(),
        )
        if loan_id is not None and (status := data.statuses.get(loan_id)) is not None:
            self.session.expected_customer_id = status.customer_id
        if caller_phone is not None:
            self.session.candidate_ids = sorted(
                cu.id for cu in data.customers.values() if cu.phone == caller_phone
            )

    # ------------------------------------------------------------------ pipeline

    def _invoke[O: BaseModel](
        self,
        tool: str,
        scope: Scope,
        make_input: Callable[[], BaseModel],
        handler: Callable[[Any], O],
        log_args: Mapping[str, Any],
    ) -> ToolResult[O]:
        """scope → validate → handler → ToolResult → audit, in the real pipeline's order."""
        self.calls.append(base.GatewayCall(tool, dict(log_args)))
        raw: dict[str, Any] = {}
        invalid: str | None = None
        inp: BaseModel | None = None
        try:
            inp = make_input()
            raw = inp.model_dump(mode="json", exclude_none=True)
        except ValidationError as exc:
            invalid = _validation_message(exc)
        result: ToolResult[O]
        denied = self._check_scope(scope)
        if denied is not None:
            result = ToolResult.failure(denied)
        elif inp is None:
            result = ToolResult.failure("invalid_input", invalid or "")
        else:
            try:
                result = ToolResult.success(handler(inp))
            except _Denied as d:
                result = ToolResult.failure(d.code, d.message)
        self._write_audit(tool, raw, result)
        return result

    def _check_scope(self, scope: Scope) -> ToolErrorCode | None:
        s = self.session
        if s.closed:
            return "session_closed"
        if scope in ("any", "worker"):
            return None
        if s.locked:
            return "locked"
        if scope == "verified" and not s.verified:
            return "not_verified"
        return None

    def _write_audit(self, tool: str, raw: dict[str, Any], result: ToolResult[Any]) -> None:
        decision: Literal["allow", "deny", "error"] = "allow"
        if not result.ok:
            assert result.error is not None
            decision = "error" if result.error.code == "upstream_error" else "deny"
        self.responses.append(result)
        self.audit.append(
            c.AuditRow(
                id=len(self.audit) + 1,
                ts=clock.now(),
                call_id=self.session.call_id,
                tool=tool,
                args_hash=args_hash(self.salt, raw),
                customer_id=self.session.bound_customer_id,
                decision=decision,
                latency_ms=0,
            )
        )

    @property
    def audit_summary(self) -> list[tuple[str, str, int | None]]:
        """`(tool, decision, customer_id)` per audit row: what tests compare across targets."""
        return [(a.tool, a.decision, a.customer_id) for a in self.audit]

    def draft_outcome(
        self, versions: Versions, *, disclosures: Sequence[str] = (), summary: str = ""
    ) -> CallOutcome:
        """What the call established so far as a `CallOutcome`, for runs whose agent has not written one yet.

        If the agent already called `record_call_outcome`, use `outcomes[-1]` instead; T-AGENT-08's outcome
        builder replaces this draft. `contact_result` is `right_party` once the caller is verified, otherwise
        `third_party` (nobody has proved they are the customer).
        """
        s = self.session
        mine = [p for p in self.data.promises if p.call_id == s.call_id]
        promise = (
            PromiseToPay(date=mine[-1].promised_date, amount=mine[-1].amount) if mine and s.verified else None
        )
        return CallOutcome(
            call_id=s.call_id,
            direction=s.direction,
            campaign=s.campaign,
            customer_id=s.bound_customer_id or s.expected_customer_id,
            loan_id=s.loan_id,
            contact_result=ContactResult.right_party if s.verified else ContactResult.third_party,
            verified=s.verified,
            disclosures_given=list(disclosures),
            promise_to_pay=promise,
            callback=self.callback,
            escalated=self.escalation,
            dispute_flag="dispute" in self.flags,
            cease_contact_flag="cease_contact" in self.flags,
            summary=summary,
            kb_sources=list(s.kb_sources),
            versions=versions,
        )

    # ------------------------------------------------------------------ data helpers

    @property
    def bound_customer(self) -> int:
        if self.session.bound_customer_id is None:
            raise _Denied("not_verified")
        return self.session.bound_customer_id

    def _customer_loans(self, loan_last4: str | None) -> list[c.Loan]:
        loans = sorted(
            (ln for ln in self.data.loans.values() if ln.customer_id == self.bound_customer),
            key=lambda ln: ln.id,
        )
        if loan_last4 is None:
            return loans
        picked = [ln for ln in loans if ln.number[-4:] == loan_last4]
        if not picked:
            raise _Denied("not_found", "no loan of yours ends in those digits")
        return picked

    def _task_customer(self) -> int | None:
        s = self.session
        if s.bound_customer_id is not None:
            return s.bound_customer_id
        if s.expected_customer_id is not None:
            return s.expected_customer_id
        return s.candidate_ids[0] if len(s.candidate_ids) == 1 else None

    # ------------------------------------------------------------------ identity tools (unverified)

    async def lookup_by_phone(self) -> ToolResult[LookupByPhoneOutput]:
        def handle(_: Any) -> LookupByPhoneOutput:
            s = self.session
            if s.direction is Direction.outbound:
                customer = self.data.customers.get(s.expected_customer_id or -1)
                if customer is None:
                    return LookupByPhoneOutput(match=False)
                return LookupByPhoneOutput(match=True, first_name=customer.full_name.split()[0])
            return LookupByPhoneOutput(match=bool(s.candidate_ids))

        return self._invoke("lookup_by_phone", "unverified", LookupByPhoneInput, handle, {})

    async def verify_identity(
        self, full_name: str, dob: dt.date, loan_last4: str
    ) -> ToolResult[VerifyIdentityOutput]:
        def handle(claim: Any) -> VerifyIdentityOutput:
            assert isinstance(claim, VerifyIdentityInput)
            first_name = self._verify(claim)
            s = self.session
            self.failed_verifications = s.failed_attempts
            return VerifyIdentityOutput(
                verified=first_name is not None,
                attempts_remaining=max(0, MAX_ATTEMPTS - s.failed_attempts),
                locked=s.locked,
                first_name=first_name,
            )

        return self._invoke(
            "verify_identity",
            "unverified",
            lambda: VerifyIdentityInput(full_name=full_name, dob=dob, loan_last4=loan_last4),
            handle,
            {"full_name": full_name, "dob": dob, "loan_last4": loan_last4},
        )

    def _verify(self, claim: VerifyIdentityInput) -> str | None:
        """The given name when the claim binds exactly one customer, else None (and a failed attempt)."""
        s = self.session
        if s.bound_customer_id is not None:  # already verified: never re-bind
            customer = self.data.customers.get(s.bound_customer_id)
            return customer.full_name.split()[0] if customer else None
        if s.direction is Direction.outbound:
            ids = [s.expected_customer_id] if s.expected_customer_id is not None else []
        else:
            ids = list(s.candidate_ids)
            if not ids:  # number not on file: narrow by DOB + last 4 (the factors are still all checked)
                ids = sorted(
                    {
                        ln.customer_id
                        for ln in self.data.loans.values()
                        if ln.number[-4:] == claim.loan_last4
                        and (cu := self.data.customers.get(ln.customer_id)) is not None
                        and cu.dob == claim.dob
                    }
                )
        matches: list[c.Customer] = []
        for customer_id in ids:
            customer = self.data.customers.get(customer_id)
            if (
                customer is None
                or customer.dob != claim.dob
                or not names_match(claim.full_name, customer.full_name)
            ):
                continue
            if any(
                ln.number[-4:] == claim.loan_last4
                for ln in self.data.loans.values()
                if ln.customer_id == customer_id
            ):
                matches.append(customer)
        if len(matches) == 1:
            s.bound_customer_id = matches[0].id
            return matches[0].full_name.split()[0]
        s.failed_attempts += 1
        if s.failed_attempts >= MAX_ATTEMPTS:
            s.locked = True
        return None

    # ------------------------------------------------------------------ policy (any)

    async def search_policy(
        self, query: str, product: ProductCode | None = None
    ) -> ToolResult[SearchPolicyOutput]:
        def handle(inp: Any) -> SearchPolicyOutput:
            assert isinstance(inp, SearchPolicyInput)
            out = self.policy(inp) if self.policy else SearchPolicyOutput(chunks=[], low_confidence=True)
            for source in out.chunks:
                self.session.record_source(source.citation)
            return out

        return self._invoke(
            "search_policy",
            "any",
            lambda: SearchPolicyInput(query=query, product=product),
            handle,
            {"query": query, "product": product},
        )

    # ------------------------------------------------------------------ verified reads

    async def get_loan_status(self, loan_last4: str | None = None) -> ToolResult[LoanStatusOutput]:
        def handle(inp: Any) -> LoanStatusOutput:
            assert isinstance(inp, GetLoanStatusInput)
            loans = []
            for loan in self._customer_loans(inp.loan_last4):
                status = self.data.statuses.get(loan.id)
                if status is not None:
                    loans.append(_summarise(status))
            return LoanStatusOutput(loans=loans)

        return self._invoke(
            "get_loan_status",
            "verified",
            lambda: GetLoanStatusInput(loan_last4=loan_last4),
            handle,
            {"loan_last4": loan_last4},
        )

    async def get_recent_transactions(self, loan_last4: str | None = None) -> ToolResult[TransactionsOutput]:
        def handle(inp: Any) -> TransactionsOutput:
            assert isinstance(inp, GetTransactionsInput)
            rows: list[c.Transaction] = []
            for loan in self._customer_loans(inp.loan_last4):
                matching = [
                    t
                    for t in self.data.transactions
                    if t.account_id == loan.repayment_account_id and t.loan_id == loan.id
                ]
                rows += sorted(matching, key=lambda t: (t.ts, t.id), reverse=True)[:MAX_TRANSACTIONS]
            rows.sort(key=lambda t: (t.ts, t.id), reverse=True)
            return TransactionsOutput(transactions=[_transaction_out(t) for t in rows[:MAX_TRANSACTIONS]])

        return self._invoke(
            "get_recent_transactions",
            "verified",
            lambda: GetTransactionsInput(loan_last4=loan_last4),
            handle,
            {"loan_last4": loan_last4},
        )

    async def get_interaction_history(self) -> ToolResult[InteractionHistoryOutput]:
        def handle(_: Any) -> InteractionHistoryOutput:
            customer_id = self.bound_customer
            loans = {ln.id for ln in self.data.loans.values() if ln.customer_id == customer_id}
            calls = sorted(
                (i for i in self.data.interactions if i.customer_id == customer_id),
                key=lambda i: i.started_at,
                reverse=True,
            )[:HISTORY_CALLS]
            return InteractionHistoryOutput(
                recent_calls=[
                    CallSummary(
                        date=i.started_at.date(),
                        direction=i.direction,
                        campaign=i.campaign,
                        contact_result=i.contact_result,
                    )
                    for i in calls
                ],
                open_promises=[
                    PromiseOut(promised_date=p.promised_date, amount=p.amount, status=p.status)
                    for p in self.data.promises
                    if p.loan_id in loans and p.status == "open"
                ],
                open_tasks=[
                    TaskOut(type=t.type, due_at=t.due_at, status=t.status)
                    for t in self.data.tasks
                    if t.customer_id == customer_id and t.status == "open"
                ],
            )

        return self._invoke("get_interaction_history", "verified", InteractionHistoryInput, handle, {})

    # ------------------------------------------------------------------ writes

    async def record_promise_to_pay(
        self, promise_date: dt.date, amount: Decimal, loan_last4: str | None = None
    ) -> ToolResult[RecordPromiseOutput]:
        def handle(inp: Any) -> RecordPromiseOutput:
            assert isinstance(inp, RecordPromiseInput)
            return self._promise(inp)

        return self._invoke(
            "record_promise_to_pay",
            "verified",
            lambda: RecordPromiseInput(promise_date=promise_date, amount=amount, loan_last4=loan_last4),
            handle,
            {"promise_date": promise_date, "amount": amount, "loan_last4": loan_last4},
        )

    def _promise(self, inp: RecordPromiseInput) -> RecordPromiseOutput:
        max_days = dt.timedelta(days=self.rules.promise_max_days)
        try:
            loans = self._customer_loans(inp.loan_last4)
        except _Denied:
            loans = []
        loan = next((ln for ln in loans if ln.id == self.session.loan_id), loans[0] if loans else None)
        status = self.data.statuses.get(loan.id) if loan else None
        if loan is None or status is None:
            return RecordPromiseOutput(
                accepted=False, reason="loan_not_found", max_date=clock.today() + max_days, amount_due=ZERO
            )
        today = status.as_of
        due = status.overdue_amount if status.overdue_amount > 0 else (status.next_due_amount or ZERO)
        reply = {"max_date": today + max_days, "amount_due": due}
        if not today <= inp.promise_date <= today + max_days:
            return RecordPromiseOutput(accepted=False, reason="date_out_of_window", **reply)
        partial_ok = self.rules.allow_partial or inp.amount == due
        if not (ZERO < inp.amount <= due) or not partial_ok:
            return RecordPromiseOutput(accepted=False, reason="amount_invalid", **reply)
        promise = c.Promise(
            id=self.data.next_id(),
            call_id=self.session.call_id,
            loan_id=loan.id,
            promised_date=inp.promise_date,
            amount=inp.amount,
            status="open",
            created_at=clock.now(),
        )
        self.data.promises.append(promise)
        return RecordPromiseOutput(accepted=True, reason="ok", promise_id=promise.id, **reply)

    def _create_task(
        self, customer_id: int, kind: str, reason: str, summary: str, due_at: dt.datetime
    ) -> c.Task:
        if customer_id not in self.data.customers:
            raise _Denied("not_found")
        task = c.Task(
            id=self.data.next_id(),
            call_id=self.session.call_id,
            customer_id=customer_id,
            loan_id=self.session.loan_id,
            type=kind,
            reason=reason,
            summary=summary,
            due_at=due_at,
            owner=None,
            status="open",
            created_at=clock.now(),
        )
        self.data.tasks.append(task)
        return task

    async def request_callback(self, preferred_at: dt.datetime, reason: str) -> ToolResult[TaskCreatedOutput]:
        def handle(inp: Any) -> TaskCreatedOutput:
            assert isinstance(inp, RequestCallbackInput)
            now = clock.now()
            if not now < inp.preferred_at <= now + MAX_CALLBACK_AHEAD:
                raise _Denied("invalid_input", "preferred_at must be in the next 30 days")
            customer_id = self._task_customer()
            if customer_id is None:
                raise _Denied("not_found", "no customer to schedule a callback for")
            task = self._create_task(customer_id, "callback", inp.reason[:200], "", inp.preferred_at)
            self.callback = Callback(datetime=inp.preferred_at)
            return TaskCreatedOutput(task_id=task.id, type=task.type, due_at=task.due_at)

        return self._invoke(
            "request_callback",
            "any",
            lambda: RequestCallbackInput(preferred_at=preferred_at, reason=reason),
            handle,
            {"preferred_at": preferred_at, "reason": reason},
        )

    async def escalate(self, reason: EscalationReason, summary: str) -> ToolResult[EscalateOutput]:
        def handle(inp: Any) -> EscalateOutput:
            assert isinstance(inp, EscalateInput)
            rules = self.rules
            customer_id = self._task_customer()
            task_id = None
            if customer_id is not None:
                task = self._create_task(
                    customer_id, "escalation", inp.reason.value, inp.summary[:600], clock.now()
                )
                task_id = task.id
            target = rules.transfer_extension if rules.human_agent_available else "callback_queue"
            self.escalation = Escalation(reason=inp.reason.value, target=target)
            return EscalateOutput(
                task_id=task_id,
                human_available=rules.human_agent_available,
                transfer_extension=rules.transfer_extension if rules.human_agent_available else None,
            )

        return self._invoke(
            "escalate",
            "any",
            lambda: EscalateInput(reason=reason, summary=summary),
            handle,
            {"reason": reason, "summary": summary},
        )

    async def set_contact_flag(
        self, flag: ContactFlag, preferred_window: TimeWindow | None = None, note: str = ""
    ) -> ToolResult[ContactFlagOutput]:
        def handle(inp: Any) -> ContactFlagOutput:
            assert isinstance(inp, SetContactFlagInput)
            customer_id = self.bound_customer
            current = self.data.prefs.get(customer_id)
            if current is None:
                raise _Denied("not_found")
            if inp.flag == "dispute":
                update: dict[str, Any] = {"dispute_open": True}
            elif inp.flag == "cease_contact":
                update = {"cease_contact": True}
            else:
                update = {"preferred_window": inp.preferred_window}
            prefs = current.model_copy(update={**update, "updated_at": clock.now()})
            self.data.prefs[customer_id] = prefs
            self.flags.add(inp.flag)
            applied = {
                "dispute": prefs.dispute_open,
                "cease_contact": prefs.cease_contact,
                "preferred_window": prefs.preferred_window == inp.preferred_window,
            }[inp.flag]
            return ContactFlagOutput(flag=inp.flag, applied=applied)

        return self._invoke(
            "set_contact_flag",
            "verified",
            lambda: SetContactFlagInput(flag=flag, preferred_window=preferred_window, note=note),
            handle,
            {"flag": flag, "preferred_window": preferred_window, "note": note},
        )

    async def record_call_outcome(self, outcome: CallOutcome) -> ToolResult[OutcomeRecordedOutput]:
        def handle(_: Any) -> OutcomeRecordedOutput:
            s = self.session
            if outcome.call_id != s.call_id:
                raise _Denied("invalid_input", "call_id does not match the session")
            if outcome.customer_id not in {None, s.bound_customer_id, s.expected_customer_id}:
                raise _Denied("forbidden", "the outcome names a customer this call was not about")
            if outcome.loan_id not in {None, s.loan_id}:
                owned = s.bound_customer_id is not None and any(
                    ln.id == outcome.loan_id and ln.customer_id == s.bound_customer_id
                    for ln in self.data.loans.values()
                )
                if not owned:
                    raise _Denied("forbidden", "the outcome names a loan this call was not about")
            sources = list(dict.fromkeys([*outcome.kb_sources, *s.kb_sources]))
            final = outcome.model_copy(update={"kb_sources": sources})
            self.data.interactions.append(
                c.Interaction(
                    call_id=s.call_id,
                    customer_id=final.customer_id,
                    loan_id=final.loan_id,
                    direction=final.direction,
                    campaign=final.campaign,
                    started_at=s.created_at,
                    ended_at=clock.now(),
                    contact_result=final.contact_result,
                    outcome=final,
                    transcript_uri=final.transcript_uri,
                    recording_uri=final.recording_uri,
                    versions=final.versions,
                )
            )
            self.outcomes.append(final)
            s.closed = True
            return OutcomeRecordedOutput(interaction_id=s.call_id)

        return self._invoke(
            "record_call_outcome", "worker", lambda: outcome, handle, {"call_id": outcome.call_id}
        )


def _summarise(s: c.LoanStatus) -> LoanSummary:
    parts = []
    if s.overdue_amount > 0 and s.oldest_unpaid_due_date is not None:
        parts.append(
            f"{speakable_money(s.overdue_amount)} overdue since {speakable_date(s.oldest_unpaid_due_date)}"
        )
    if s.next_due_date is not None and s.next_due_amount is not None:
        parts.append(
            f"next payment {speakable_money(s.next_due_amount)} due on {speakable_date(s.next_due_date)}"
        )
    return LoanSummary(
        loan_masked=f"****{s.number_last4}",
        product_code=s.product_code,
        product_name=s.product_name,
        outstanding_balance=s.outstanding_balance,
        overdue_amount=s.overdue_amount,
        oldest_unpaid_due_date=s.oldest_unpaid_due_date,
        next_due_date=s.next_due_date,
        next_due_amount=s.next_due_amount,
        days_past_due=s.days_past_due,
        fees_accrued=s.fees_accrued,
        status=s.status,
        speakable="; ".join(parts) or "no payment due",
    )


def _transaction_out(t: c.Transaction) -> TransactionOut:
    when = t.ts.date()
    spoken = f"{speakable_money(t.amount)} on {speakable_date(when)} by {t.channel}"
    return TransactionOut(
        date=when,
        amount=t.amount,
        channel=t.channel,
        status=t.status,
        reference_masked=mask_reference(t.reference),
        speakable=spoken + (" (pending, not yet posted)" if t.status == "pending" else ""),
    )


# --------------------------------------------------------------------------- scripted model

MISBEHAVE_MODES = ("call_get_loan_status_first",)
Policy = Callable[[ChatContext, Sequence[str]], base.Step]


class ScriptedLLM(base.ScriptedLLM):
    """`agent.testing.ScriptedLLM` plus a default policy and `misbehave`.

    - `steps`: replayed in order, exactly as the base class does. Without `steps` the `policy` answers each
      request instead (see `follow_expected_tools`).
    - `misbehave="call_get_loan_status_first"`: the first request gets a `get_loan_status` call whatever the
      state asks, before the script or policy goes on. When the state offers that tool (so the call reaches
      the gateway) the gateway must deny it as `not_verified`: verification is a code guard, not a prompt.
      A state that does not offer the tool makes LiveKit reject the call instead, which is also a pass.
    """

    def __init__(
        self,
        steps: Sequence[base.ScriptItem] | None = None,
        *,
        policy: Policy | None = None,
        misbehave: str | None = None,
    ) -> None:
        if misbehave is not None and misbehave not in MISBEHAVE_MODES:
            raise ValueError(f"unknown misbehave mode {misbehave!r}; choose from {MISBEHAVE_MODES}")
        super().__init__(steps or [])
        self._scripted = steps is not None
        self._policy = policy or follow_expected_tools([])
        self.misbehave = misbehave
        self._misbehaved = False

    def take(self, chat_ctx: ChatContext, tools: Sequence[Tool]) -> base.Step:
        if self.misbehave == "call_get_loan_status_first" and not self._misbehaved:
            self._misbehaved = True
            self.requests.append(tuple(sorted(get_fnc_tool_names(tools))))
            return base.call("get_loan_status")
        if self._scripted:
            return super().take(chat_ctx, tools)
        offered = tuple(sorted(get_fnc_tool_names(tools)))
        self.requests.append(offered)
        return self._policy(chat_ctx, offered)

    @property
    def remaining(self) -> int:
        return super().remaining if self._scripted else 0


def follow_expected_tools(
    expected: Sequence[base.ToolCallSpec],
    *,
    reply: str = "Thank you.",
    after_tool: str = "Thanks, noted.",
) -> Policy:
    """Default policy: call the next `expected` tool whenever the state offers it (after the caller speaks, or
    after the previous tool's result); otherwise say `after_tool` following a tool result, else `reply`.
    Deterministic, no model."""
    queue = list(expected)

    def policy(chat_ctx: ChatContext, offered: Sequence[str]) -> base.Step:
        if queue and queue[0].name in offered:
            return base.Step(calls=(queue.pop(0),))
        items = chat_ctx.items
        last = items[-1] if items else None
        if last is not None and getattr(last, "type", "") == "function_call_output":
            return base.say(after_tool)
        return base.say(reply)

    return policy
