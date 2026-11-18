"""A JSON-lines log of the session's turn-taking events, for the live barge-in check (VOICE-R4).

`attach_event_log(session, path)` appends one line per `user_state_changed` / `agent_state_changed` event:

    {"type": "agent_state_changed", "new_state": "speaking", "created_at": 1763369000.12}

`tests/voice/test_live_barge_in.py` reads this file and measures how fast the agent stopped speaking after the
caller started talking over it (`session.barge_in_latency_s`). Only states and timestamps are written, never
what anyone said. It is a development aid: the worker attaches it only when `SESSION_EVENT_LOG` is set (the
`make agent-dev` target sets it).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

EVENT_TYPES = ("user_state_changed", "agent_state_changed")


def attach_event_log(session: Any, path: Path, *, clock: Callable[[], float] = time.time) -> None:
    """Append every turn-taking state change of `session` to `path` (created, with its folder, on demand)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def writer(event_type: str) -> Callable[[Any], None]:
        def write(event: Any) -> None:
            record = {
                "type": event_type,
                "new_state": str(event.new_state),
                "created_at": float(getattr(event, "created_at", None) or clock()),
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

        return write

    for event_type in EVENT_TYPES:
        session.on(event_type, writer(event_type))
