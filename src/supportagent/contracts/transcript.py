"""Call transcript turns (OBS-R4), owned by the observability module.

`RawTurn` is what the agent worker hands to the transcript store: it may contain PII, and it carries
`known_pii` (values the call knows are sensitive). `TranscriptTurn` is what is stored and read back:
its text and tool arguments are already redacted and `known_pii` is gone. One `TranscriptTurn` is one
JSON line of `var/transcripts/{call_id}.jsonl`.
"""

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

Speaker = Literal["agent", "caller", "system"]
ToolDecision = Literal["allowed", "denied"]


def _aware(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    return value.astimezone(dt.UTC)


UtcDatetime = Annotated[dt.datetime, AfterValidator(_aware)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolRef(_Contract):
    """A tool call attached to a turn. `args` is redacted by the store before it is written."""

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    decision: ToolDecision
    latency_ms: int | None = Field(default=None, ge=0)


class RedactionInfo(_Contract):
    """What the redactor found in a turn's text; `fallback` is True when only the regex pass ran."""

    entities: list[str] = Field(default_factory=list)
    fallback: bool = False


class RawTurn(_Contract):
    """Input to `TranscriptStore.append`. Never persisted as is."""

    speaker: Speaker
    text: str
    ts: UtcDatetime
    state: str | None = None
    tool: ToolRef | None = None
    known_pii: list[str] = Field(default_factory=list)


class TranscriptTurn(_Contract):
    """One stored line: redacted, numbered (`seq` starts at 1 per call) and tagged."""

    call_id: str
    seq: int = Field(ge=1)
    ts: UtcDatetime
    speaker: Speaker
    text: str
    state: str | None = None
    tool: ToolRef | None = None
    redaction: RedactionInfo = Field(default_factory=RedactionInfo)
