"""The warm-transfer port the escalation state calls (AGENT-R9). TEL-04 implements it; tests inject a fake.

`Transferer` lives in the agent package because the agent owns the call flow and telephony only adapts it
to SIP; the agent never imports telephony.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TransferResult:
    ok: bool
    detail: str = ""  # a short machine-readable reason on failure, never caller data


class Transferer(Protocol):
    async def transfer(self, room: str, participant_identity: str, to_extension: str) -> TransferResult:
        """Move the caller (`participant_identity` in `room`) to a human at `to_extension`."""
        ...
