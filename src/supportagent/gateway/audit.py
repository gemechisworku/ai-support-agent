"""Audit trail (GW-R8, NFR-AUDIT): one row per tool or service request, allowed or denied.

Rows go to the bank's append-only `audit_log` through `POST /audit` *before* a result is returned. If
the write fails the request fails closed (`audit_unavailable`, no data). Arguments are never stored:
`args_hash = sha256(salt + canonical_json(args))`, so equal arguments can be correlated without the
values (names, dates of birth) ever leaving the gateway.
"""

import hashlib
import json
from typing import Any, Literal

from supportagent.common import clock
from supportagent.contracts.bank import AuditIn
from supportagent.gateway.bank_client import BankClient, BankError

Decision = Literal["allow", "deny", "error"]


class AuditUnavailable(RuntimeError):
    """The audit row could not be written; the caller must not return data."""


def canonical_json(args: Any) -> str:
    return json.dumps(args if args is not None else {}, sort_keys=True, separators=(",", ":"), default=str)


def args_hash(salt: str, args: Any) -> str:
    return hashlib.sha256((salt + canonical_json(args)).encode()).hexdigest()


class AuditWriter:
    def __init__(self, bank: BankClient, salt: str) -> None:
        self._bank = bank
        self._salt = salt

    async def write(
        self,
        *,
        call_id: str,
        tool: str,
        args: Any,
        customer_id: int | None,
        decision: Decision,
        latency_ms: int,
    ) -> None:
        row = AuditIn(
            ts=clock.now(),
            call_id=call_id,
            tool=tool,
            args_hash=args_hash(self._salt, args),
            customer_id=customer_id,
            decision=decision,
            latency_ms=max(0, latency_ms),
        )
        try:
            await self._bank.append_audit(row)
        except BankError as exc:
            raise AuditUnavailable(str(exc)) from exc
