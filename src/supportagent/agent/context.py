"""Call context: parse `CallMetadata` and hold the per-call state (AGENT-R1).

- `parse_call_metadata` reads the room metadata JSON (outbound, written by the dispatcher) or the SIP
  attributes (inbound). A room with neither is a **playground** call. A missing `call_id` is generated.
- `CallState` is the session's `userdata`. It holds the gateway port and the session token, which is
  never rendered into a prompt, a repr or a log line.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr, ValidationError

from supportagent.agent.gateway_client import GatewayPort
from supportagent.contracts.call import CallMetadata
from supportagent.contracts.outcome import Campaign, Direction

SIP_PHONE_ATTRIBUTE = "sip.phoneNumber"  # LiveKit: the caller's number on an inbound SIP call
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class InvalidCallMetadata(ValueError):
    """The room metadata is not usable. The message never contains the metadata itself."""


def _new_call_id() -> str:
    return str(uuid.uuid4())


def parse_call_metadata(
    room_metadata: str | None,
    sip_attributes: Mapping[str, str] | None = None,
    *,
    new_call_id: Callable[[], str] = _new_call_id,
) -> CallMetadata:
    """Room metadata JSON (outbound), else SIP attributes (inbound), else a playground call."""
    raw = (room_metadata or "").strip()
    if raw:
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidCallMetadata("room metadata is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise InvalidCallMetadata("room metadata must be a JSON object")
        fields = dict(payload)
        fields.setdefault("call_id", new_call_id())
        try:
            return CallMetadata.model_validate(fields)
        except ValidationError as exc:
            bad = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
            raise InvalidCallMetadata(f"room metadata has invalid fields: {', '.join(bad)}") from exc

    phone = (sip_attributes or {}).get(SIP_PHONE_ATTRIBUTE)
    if phone:
        return CallMetadata(
            call_id=new_call_id(),
            direction=Direction.inbound,
            campaign=Campaign.inbound,
            caller_phone=phone if _E164.match(phone) else None,
        )
    return CallMetadata(call_id=new_call_id(), direction=Direction.inbound, campaign=Campaign.playground)


@dataclass
class CallState:
    """Per-call state, passed as the `AgentSession`'s `userdata`. States depend on `GatewayPort` only."""

    metadata: CallMetadata
    gateway: GatewayPort = field(repr=False)
    session_token: SecretStr = field(repr=False)
    verified: bool = False
    locked: bool = False
    first_name: str | None = None
    states_visited: list[str] = field(default_factory=list)  # BaseStateAgent.on_enter appends, in order
    concerns: list[str] = field(default_factory=list)  # kinds raised through `report_concern`

    @property
    def call_id(self) -> str:
        return self.metadata.call_id

    def outcome_so_far(self) -> dict[str, Any]:
        """A snapshot of what the call has established so far (the draft the text harness returns).

        T-AGENT-08's `OutcomeBuilder` replaces this with the validated `CallOutcome`.
        """
        return {
            "call_id": self.call_id,
            "direction": self.metadata.direction.value,
            "campaign": self.metadata.campaign.value,
            "verified": self.verified,
            "locked": self.locked,
            "states": list(self.states_visited),
            "concerns": list(self.concerns),
        }
