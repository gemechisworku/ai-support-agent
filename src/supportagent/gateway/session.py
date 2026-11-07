"""Per-call sessions (GW-R2): one session per call, an opaque token, and identity binding.

The agent worker's code (never the LLM) creates a session through `POST /sessions` and keeps the
token out of every prompt. A session starts unverified. `verify_identity` (gateway/identity.py) is
the only code that binds a customer; no tool accepts a customer id. Sessions live in memory (one
gateway process in the POC), expire after 30 min idle or 60 min total, and lock after 2 failed
verifications.
"""

import datetime as dt
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field

from supportagent.common import clock
from supportagent.contracts.gateway import SessionCreate
from supportagent.contracts.outcome import Campaign, Direction
from supportagent.gateway.bank_client import BankClient
from supportagent.gateway.config import MAX_ATTEMPTS, SESSION_IDLE_TTL, SESSION_MAX_TTL


@dataclass
class Session:
    call_id: str
    token: str
    direction: Direction
    campaign: Campaign | None
    loan_id: int | None
    caller_phone: str | None
    created_at: dt.datetime
    last_seen: dt.datetime
    # Who may be bound: the loan's customer (outbound) or the phone's customers (inbound). Never serialised.
    expected_customer_id: int | None = None
    candidate_ids: list[int] = field(default_factory=list)
    bound_customer_id: int | None = None
    failed_attempts: int = 0
    locked: bool = False
    closed: bool = False
    kb_sources: list[str] = field(default_factory=list)
    tool_calls: int = 0

    @property
    def verified(self) -> bool:
        return self.bound_customer_id is not None

    @property
    def attempts_remaining(self) -> int:
        return max(0, MAX_ATTEMPTS - self.failed_attempts)

    def record_source(self, citation: str) -> None:
        if citation not in self.kb_sources:
            self.kb_sources.append(citation)


class SessionStore:
    """In-memory sessions keyed by token and by call id."""

    def __init__(
        self,
        now: Callable[[], dt.datetime] = clock.now,
        idle_ttl: dt.timedelta = SESSION_IDLE_TTL,
        max_ttl: dt.timedelta = SESSION_MAX_TTL,
    ) -> None:
        self._now = now
        self._idle = idle_ttl
        self._max = max_ttl
        self._by_token: dict[str, Session] = {}
        self._by_call: dict[str, Session] = {}

    async def create(self, req: SessionCreate, bank: BankClient) -> Session:
        """Create the call's session, or return the existing one (idempotent per `call_id`)."""
        existing = self._by_call.get(req.call_id)
        if existing is not None and self._alive(existing):
            return existing
        now = self._now()
        session = Session(
            call_id=req.call_id,
            token=secrets.token_urlsafe(32),
            direction=req.direction,
            campaign=req.campaign,
            loan_id=req.loan_id,
            caller_phone=req.caller_phone,
            created_at=now,
            last_seen=now,
        )
        if req.loan_id is not None:
            status = await bank.loan_status(req.loan_id)
            session.expected_customer_id = status.customer_id if status else None
        if req.caller_phone is not None:
            session.candidate_ids = [cu.id for cu in await bank.customers_by_phone(req.caller_phone)]
        self._by_token[session.token] = session
        self._by_call[session.call_id] = session
        return session

    def resolve(self, token: str | None) -> Session | None:
        """The live session for a token, or None (missing, unknown or expired). Touches `last_seen`."""
        if not token:
            return None
        session = self._by_token.get(token)
        if session is None:
            return None
        if not self._alive(session):
            self._drop(session)
            return None
        session.last_seen = self._now()
        return session

    def close(self, call_id: str) -> bool:
        session = self._by_call.get(call_id)
        if session is None:
            return False
        session.closed = True
        return True

    def get(self, call_id: str) -> Session | None:
        return self._by_call.get(call_id)

    def _alive(self, session: Session) -> bool:
        now = self._now()
        return now - session.last_seen <= self._idle and now - session.created_at <= self._max

    def _drop(self, session: Session) -> None:
        self._by_token.pop(session.token, None)
        if self._by_call.get(session.call_id) is session:
            self._by_call.pop(session.call_id, None)


def bind_customer(session: Session, customer_id: int) -> None:
    """Bind the verified customer to the session. Only `gateway/identity.py` calls this (tested)."""
    if session.locked or session.closed:
        raise PermissionError("cannot bind a locked or closed session")
    if session.bound_customer_id is not None and session.bound_customer_id != customer_id:
        raise PermissionError("session is already bound to another customer")
    session.bound_customer_id = customer_id


def record_failure(session: Session) -> None:
    session.failed_attempts += 1
    if session.failed_attempts >= MAX_ATTEMPTS:
        session.locked = True
