"""`RunRecord`: everything one scenario run left behind, as a stable JSON document (EVAL-R1).

Guardrail checkers (T-EVAL-03), the report and the review sheet all read this one shape. Ids and timestamps
come from the frozen clock and the scenario id, so two runs of a scripted scenario serialise identically.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from supportagent.contracts.bank import AuditRow
from supportagent.contracts.outcome import CallOutcome, Versions
from supportagent.contracts.transcript import TranscriptTurn

Mode = Literal["scripted", "simulated"]
Decision = Literal["allow", "deny", "error"]


class ToolCallLog(BaseModel):
    """One request the gateway saw, with its decision (the audit row) and result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int = Field(ge=1)
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    error_code: str | None = None
    decision: Decision


class ModelCall(BaseModel):
    """A tool the model asked for, even one that never reached the gateway (the state has no such tool)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int = Field(ge=1)
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    is_error: bool = False


class RunRecord(BaseModel):
    """One run: transcript, tool calls with decisions, audit rows, the call outcome and the bank snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    mode: Mode
    call_id: str
    turns: list[TranscriptTurn]
    tool_calls: list[ToolCallLog]  # gateway requests, in order, each with its decision
    model_calls: list[ModelCall]  # what the model called, in order
    audit: list[AuditRow]
    outcome: CallOutcome
    bank_snapshot: dict[str, Any]
    llm_model: str
    versions: Versions
    states: list[str] = Field(default_factory=list)

    def to_json(self) -> str:
        """JSON in field order, two-space indent, trailing newline: byte-stable for equal records."""
        return self.model_dump_json(indent=2) + "\n"

    @property
    def agent_text(self) -> str:
        """Everything the agent said, one line per turn."""
        return "\n".join(t.text for t in self.turns if t.speaker == "agent")
