"""Identity verification (GW-R3, F4): the only code that binds a customer to a session.

Who can be bound:
- **outbound** — only the customer who owns the called loan (`session.expected_customer_id`);
- **inbound** — the customers registered to the caller's phone number (`session.candidate_ids`).
  When the number is not on file, the customers whose date of birth and loan last 4 match the claim
  (`GET /customers/match`, T-GW-11); every factor below, including the name, is still checked.

A candidate matches when its given- and family-name tokens each match the spoken name within one
edit (speech-to-text spelling), the date of birth is equal, and the last 4 digits belong to one of
the candidate's loans. Exactly one match binds; zero or several count as a failed attempt, and the
second failure locks the session. The result never says which factor failed.
"""

import re
import unicodedata
from dataclasses import dataclass

from supportagent.contracts.gateway import VerifyIdentityInput
from supportagent.gateway.bank_client import BankClient
from supportagent.gateway.session import Session, bind_customer, record_failure


def normalise_name(name: str) -> list[str]:
    """Casefold, strip accents and punctuation, split into tokens: "José  O'Neil" → ["jose", "oneil"]."""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^\w\s]", "", ascii_only.casefold())
    return cleaned.split()


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def names_match(spoken: str, registered: str, tolerance: int = 1) -> bool:
    """Given name and family name must each match within `tolerance` edits (middle names ignored)."""
    said, known = normalise_name(spoken), normalise_name(registered)
    if len(said) < 2 or len(known) < 2:
        return False
    return levenshtein(said[0], known[0]) <= tolerance and levenshtein(said[-1], known[-1]) <= tolerance


@dataclass(frozen=True)
class VerifyResult:
    verified: bool
    first_name: str | None


def candidates(session: Session) -> list[int]:
    if session.direction == "outbound":
        return [session.expected_customer_id] if session.expected_customer_id is not None else []
    return list(session.candidate_ids)


async def claim_candidates(session: Session, claim: VerifyIdentityInput, bank: BankClient) -> list[int]:
    """Who this claim may bind. An inbound caller whose number is not on file is looked up by DOB +
    loan last 4 instead; that only narrows the search, `verify` still checks every factor."""
    ids = candidates(session)
    if session.direction == "inbound" and not ids:
        ids = sorted({m.customer_id for m in await bank.match_customers(claim.dob, claim.loan_last4)})
    return ids


async def verify(session: Session, claim: VerifyIdentityInput, bank: BankClient) -> VerifyResult:
    if session.bound_customer_id is not None:  # already verified: never re-bind or un-bind
        customer = await bank.get_customer(session.bound_customer_id)
        return VerifyResult(True, normalise_first(customer.full_name) if customer else None)
    matches: list[tuple[int, str]] = []
    for customer_id in await claim_candidates(session, claim, bank):
        customer = await bank.get_customer(customer_id)
        if (
            customer is None
            or customer.dob != claim.dob
            or not names_match(claim.full_name, customer.full_name)
        ):
            continue
        loans = await bank.customer_loans(customer_id)
        if any(loan.number[-4:] == claim.loan_last4 for loan in loans):
            matches.append((customer_id, customer.full_name))
    if len(matches) == 1:
        customer_id, full_name = matches[0]
        bind_customer(session, customer_id)
        return VerifyResult(True, normalise_first(full_name))
    record_failure(session)
    return VerifyResult(False, None)


def normalise_first(full_name: str) -> str:
    """The given name as registered (for "Thank you, Frank")."""
    return full_name.split()[0]
