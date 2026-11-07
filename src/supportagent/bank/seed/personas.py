"""The 12 scripted personas (BANK-R3; specs/02-mock-bank/plan.md, architecture §4).

Each persona has a fixed customer id (9001-9012), phone (+155501003NN, softphone extension 3NN), date
of birth and loan number, and is placed in exactly its situation relative to `as_of`. Persona rows do
not depend on the population seed. `PERSONAS` is pure data, importable without a database: evals
(T-EVAL) and telephony provisioning (T-TEL) read it.
"""

import datetime as dt
import itertools
import random
from dataclasses import dataclass
from decimal import Decimal

from supportagent.bank import ledger
from supportagent.bank.seed.builder import LoanPlan, SeedData, build_loan
from supportagent.bank.seed.products import PRODUCTS
from supportagent.contracts.outcome import CallOutcome

PERSONA_SEED = 9000
TX_ID_START = 9_000_000
BIANCA_PROMISE_CALL_ID = "b1a2c3d4-0000-4000-8000-000000009012"


@dataclass(frozen=True)
class Persona:
    customer_id: int
    name: str
    phone: str
    extension: str
    dob: dt.date
    product_code: str
    loan_id: int
    loan_number: str
    situation: str
    expected_outcome: str
    days_past_due: int = 0
    next_due_in_days: int | None = None  # for current loans: days from as_of to the next due date

    @property
    def loan_last4(self) -> str:
        return self.loan_number[-4:]

    @property
    def first_name(self) -> str:
        return self.name.split()[0]


@dataclass(frozen=True)
class _LoanTerms:
    principal: Decimal
    apr: Decimal
    term: int
    anchor_seq: int


def _p(
    cid: int, name: str, dob: str, code: str, last4: str, situation: str, outcome: str, **kw: int
) -> Persona:
    n = cid - 9000
    return Persona(
        customer_id=cid,
        name=name,
        phone=f"+155501003{n:02d}",
        extension=f"3{n:02d}",
        dob=dt.date.fromisoformat(dob),
        product_code=code,
        loan_id=cid,
        loan_number=f"552026{last4}",
        situation=situation,
        expected_outcome=outcome,
        **kw,
    )


PERSONAS: list[Persona] = [
    _p(
        9001,
        "Olivia Bennett",
        "1990-07-22",
        "PERSONAL",
        "2861",
        "Due in 2 days, never late",
        "Reminder, intent to pay confirmed",
        next_due_in_days=2,
    ),
    _p(
        9002,
        "Frank Fischer",
        "1985-03-14",
        "PERSONAL",
        "4417",
        "5 days past due, first ever miss",
        "Reason 'forgot', promise to pay in 3 days",
        days_past_due=5,
    ),
    _p(
        9003,
        "Hana Tesfaye",
        "1979-11-02",
        "AUTO",
        "7350",
        "22 days past due, lost job; two split payments before",
        "Hardship referral, escalated to a human",
        days_past_due=22,
    ),
    _p(
        9004,
        "Dawit Bekele",
        "1971-05-30",
        "SME_WC",
        "5128",
        "12 days past due; disputes the late fee",
        "Dispute flag set, collection paused, human",
        days_past_due=12,
    ),
    _p(
        9005,
        "Priya Raman",
        "1993-02-11",
        "PERSONAL",
        "6094",
        "3 days past due; a pending transfer equals the amount due",
        "Payment in transit, no promise needed",
        days_past_due=3,
    ),
    _p(
        9006,
        "Marcus Bell",
        "1968-09-19",
        "MORTGAGE",
        "1736",
        "8 days past due; a third party (spouse) answers",
        "Neutral message, no details given",
        days_past_due=8,
    ),
    _p(
        9007,
        "Sam Carter",
        "1982-12-05",
        "PERSONAL",
        "8842",
        "15 days past due; asks never to be called",
        "Cease-contact flag set; the scheduler excludes him",
        days_past_due=15,
    ),
    _p(
        9008,
        "Cleo Park",
        "1988-04-27",
        "MORTGAGE",
        "3905",
        "Current; calls in about early repayment",
        "Policy answer with citation",
        next_due_in_days=12,
    ),
    _p(
        9009,
        "Leo Moreau",
        "1991-08-08",
        "AUTO",
        "2279",
        "6 days past due; fails verification twice",
        "Verification locked, neutral callback message",
        days_past_due=6,
    ),
    _p(
        9010,
        "Anton Petrov",
        "1996-01-17",
        "SALARY_ADV",
        "6613",
        "9 days past due; angry",
        "Stays calm, escalates on request",
        days_past_due=9,
    ),
    _p(
        9011,
        "Wendy Shaw",
        "1987-06-03",
        "PERSONAL",
        "9451",
        "4 days past due; the number now belongs to someone else",
        "Wrong number recorded, no details given",
        days_past_due=4,
    ),
    _p(
        9012,
        "Bianca Russo",
        "1984-10-21",
        "PERSONAL",
        "7068",
        "10 days past due; promise dated yesterday was missed",
        "Callback about the broken promise; new promise or escalation",
        days_past_due=10,
    ),
]

PERSONA_BY_ID = {p.customer_id: p for p in PERSONAS}

_TERMS: dict[int, _LoanTerms] = {
    9001: _LoanTerms(Decimal("8000"), Decimal("11.90"), 36, 14),
    9002: _LoanTerms(Decimal("6000"), Decimal("12.50"), 36, 18),
    9003: _LoanTerms(Decimal("24000"), Decimal("7.90"), 60, 25),
    9004: _LoanTerms(Decimal("40000"), Decimal("13.25"), 24, 10),
    9005: _LoanTerms(Decimal("5000"), Decimal("10.50"), 24, 9),
    9006: _LoanTerms(Decimal("280000"), Decimal("6.10"), 120, 40),
    9007: _LoanTerms(Decimal("12000"), Decimal("14.75"), 48, 20),
    9008: _LoanTerms(Decimal("210000"), Decimal("5.95"), 120, 30),
    9009: _LoanTerms(Decimal("18000"), Decimal("8.40"), 48, 16),
    9010: _LoanTerms(Decimal("1200"), Decimal("29.90"), 3, 2),
    9011: _LoanTerms(Decimal("4000"), Decimal("16.20"), 24, 7),
    9012: _LoanTerms(Decimal("7000"), Decimal("13.90"), 36, 12),
}


def _instalment_amount(t: _LoanTerms) -> Decimal:
    principal, interest = ledger.amortize(t.principal, t.apr, t.term)[t.anchor_seq - 1]
    return ledger.money(principal + interest)


def generate_personas(as_of: dt.date) -> SeedData:
    rng = random.Random(PERSONA_SEED)  # noqa: S311  (deterministic synthetic data)
    tx_ids = itertools.count(TX_ID_START)
    data = SeedData()
    midnight = dt.datetime(as_of.year, as_of.month, as_of.day, tzinfo=dt.UTC)
    for p in PERSONAS:
        first, last = p.name.split()
        data.customers.append(
            {
                "id": p.customer_id,
                "full_name": p.name,
                "dob": p.dob,
                "phone": p.phone,
                "email": f"{first}.{last}@example.com".lower(),
                "address": f"{p.extension} Persona Lane, Springfield, NY 10001",
                "timezone": "America/New_York",
                "segment": "sme" if p.product_code == "SME_WC" else "retail",
                "kyc_status": "verified",
                "preferred_language": "en",
            }
        )
        data.contact_preferences.append(
            {
                "customer_id": p.customer_id,
                "consent_voice_ai": True,
                "dnc": False,
                "preferred_window": None,
                "cease_contact": False,
                "dispute_open": False,
                "updated_at": midnight - dt.timedelta(days=90),
            }
        )
        data.accounts.append(
            {
                "id": p.customer_id,
                "customer_id": p.customer_id,
                "type": "current",
                "number": f"{390_000_000_000 + p.customer_id:012d}",
                "balance": Decimal("1250.00"),
                "status": "active",
            }
        )
        t = _TERMS[p.customer_id]
        if p.next_due_in_days is not None:
            anchor = as_of + dt.timedelta(days=p.next_due_in_days)
        else:
            anchor = as_of - dt.timedelta(days=p.days_past_due)
        plan = LoanPlan(
            loan_id=p.loan_id,
            customer_id=p.customer_id,
            account_id=p.customer_id,
            product=PRODUCTS[p.product_code],
            number=p.loan_number,
            principal=t.principal,
            apr=t.apr,
            term=t.term,
            anchor=anchor,
            anchor_seq=t.anchor_seq,
            on_time_history=True,
            split_history=2 if p.customer_id == 9003 else 0,
            pending_payment=_instalment_amount(t) if p.customer_id == 9005 else ledger.ZERO,
        )
        loan, insts, txs = build_loan(plan, as_of, rng, tx_ids)
        data.loans.append(loan)
        data.installments.extend(insts)
        data.transactions.extend(txs)

    # Broken-promise Bianca: an earlier call (8 days ago, outside the 7-day cool-off) produced a promise
    # dated yesterday that was not kept.
    bianca = PERSONA_BY_ID[9012]
    amount = _instalment_amount(_TERMS[9012])
    called = midnight - dt.timedelta(days=8) + dt.timedelta(hours=15)
    outcome = CallOutcome.model_validate(
        {
            "call_id": BIANCA_PROMISE_CALL_ID,
            "direction": "outbound",
            "campaign": "missed_payment",
            "customer_id": bianca.customer_id,
            "loan_id": bianca.loan_id,
            "contact_result": "right_party",
            "verified": True,
            "disclosures_given": ["ai_recording@1.0", "collections@1.0"],
            "intent": ["promise_to_pay"],
            "reason_code": "insufficient_funds",
            "promise_to_pay": {"date": as_of - dt.timedelta(days=1), "amount": amount},
            "sentiment": "neutral",
            "summary": "Customer was short of funds until payday and promised to pay the overdue instalment.",
            "versions": {"prompt": "seed", "policy": "seed", "model": "seed"},
        }
    )
    data.interactions.append(
        {
            "call_id": BIANCA_PROMISE_CALL_ID,
            "customer_id": bianca.customer_id,
            "loan_id": bianca.loan_id,
            "direction": "outbound",
            "campaign": "missed_payment",
            "started_at": called,
            "ended_at": called + dt.timedelta(minutes=4),
            "contact_result": "right_party",
            "outcome": outcome.model_dump(mode="json"),
            "transcript_uri": None,
            "recording_uri": None,
            "versions": outcome.versions.model_dump(mode="json"),
        }
    )
    data.promises_to_pay.append(
        {
            "call_id": BIANCA_PROMISE_CALL_ID,
            "loan_id": bianca.loan_id,
            "promised_date": as_of - dt.timedelta(days=1),
            "amount": amount,
            "status": "open",
            "created_at": called + dt.timedelta(minutes=3),
        }
    )
    return data
