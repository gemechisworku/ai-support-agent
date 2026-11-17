"""Live, manual-assisted barge-in timing check (T-VOICE-03 / VOICE-R4: TTS stops < 300 ms after the caller
starts talking over it). Marker `live`: never in CI.

How to run it: start the agent with the model servers up (`make models-up`) and log the session's
`user_state_changed` / `agent_state_changed` events as JSON lines (the browser playground, T-VOICE-05, will do
this; until then add a two-line `session.on(...)` logger to the worker).

1. Let the agent speak a long answer (ask about a late-fee policy) and talk over it mid-sentence.
   Repeat a few times.
2. `BARGE_IN_EVENT_LOG=var/log/session_events.jsonl \\
   uv run pytest -m live tests/voice/test_live_barge_in.py -s`

Each line: `{"type": "agent_state_changed" | "user_state_changed", "new_state": "...",
"created_at": <epoch s>}`.
The test passes when every barge-in in the log stopped the agent within 0.3 s, and fails if the log holds
none (so a missing interruption cannot pass silently).
"""

import json
import os
from pathlib import Path

import pytest

from supportagent.voice.session import barge_in_latency_s

pytestmark = pytest.mark.live

BUDGET_S = 0.3


def _split_into_turns(events: list[dict]) -> list[list[dict]]:
    """One group per agent speaking spell, so several barge-ins in one log are each measured."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    for ev in sorted(events, key=lambda e: e["created_at"]):
        if ev["type"] == "agent_state_changed" and ev["new_state"] == "speaking" and current:
            groups.append(current)
            current = []
        current.append(ev)
    if current:
        groups.append(current)
    return groups


def test_barge_in() -> None:
    path = os.environ.get("BARGE_IN_EVENT_LOG")
    if not path:
        pytest.skip("set BARGE_IN_EVENT_LOG to a JSON-lines session event log (see the module docstring)")
    events = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    latencies = [x for x in (barge_in_latency_s(g) for g in _split_into_turns(events)) if x is not None]
    print(f"barge-in latencies (s): {[round(x, 3) for x in latencies]}")
    assert latencies, "no barge-in in the log: talk over the agent while it is speaking"
    assert max(latencies) < BUDGET_S, f"slowest barge-in {max(latencies):.3f}s exceeds {BUDGET_S}s"
