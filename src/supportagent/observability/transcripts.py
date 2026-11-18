"""Append-only, redacted JSONL transcripts: one file per call (OBS-R4).

    store = TranscriptStore()                     # base dir from TRANSCRIPTS_DIR (default var/transcripts)
    store.append(call_id, RawTurn(...))           # redacts text and tool args, assigns seq, writes a line
    store.read(call_id)                           # -> list[TranscriptTurn] ordered by seq
    store.uri(call_id)                            # -> "file://var/transcripts/<call_id>.jsonl"

Redaction cannot be bypassed: `append` is the only way to write, it accepts only a `RawTurn`, and it
always runs `redaction.redact` / `redact_mapping` first. `known_pii` is used for redaction and is never
written. `seq` is the next line number, assigned under an exclusive `fcntl.flock` on the file, so
concurrent writers (threads or processes) get gapless, unique numbers.
"""

import fcntl
import os
import re
from pathlib import Path

from supportagent.common.settings import get_settings
from supportagent.contracts.transcript import RawTurn, RedactionInfo, ToolRef, TranscriptTurn
from supportagent.observability import redaction

# UUIDs and plain slugs; no path separators, dots or spaces, so a call id can never leave the base dir.
_CALL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def _check_call_id(call_id: str) -> str:
    if not isinstance(call_id, str) or not _CALL_ID.fullmatch(call_id):
        raise ValueError("invalid call_id: use letters, digits, '-' and '_' only (max 128 characters)")
    return call_id


class TranscriptStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else get_settings().data.transcripts_dir

    def _path(self, call_id: str) -> Path:
        return self.base_dir / f"{_check_call_id(call_id)}.jsonl"

    def uri(self, call_id: str) -> str:
        """`file://` plus the transcript path, relative to the repo when the base dir is relative."""
        return f"file://{self._path(call_id).as_posix()}"

    def append(self, call_id: str, turn: RawTurn) -> TranscriptTurn:
        """Redact `turn`, give it the next `seq` and append it as one JSON line. Returns what was stored."""
        path = self._path(call_id)
        if not isinstance(turn, RawTurn):
            raise TypeError("append() takes a RawTurn; text is always redacted by the store")
        known = tuple(v for v in turn.known_pii if v)
        red = redaction.redact(turn.text, known=known)
        tool = None
        if turn.tool is not None:
            tool = ToolRef(
                name=turn.tool.name,
                args=redaction.redact_mapping(turn.tool.args, known=known),
                decision=turn.tool.decision,
                latency_ms=turn.tool.latency_ms,
            )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o640)
        with os.fdopen(fd, "r+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                seq = f.read().count(b"\n") + 1
                stored = TranscriptTurn(
                    call_id=call_id,
                    seq=seq,
                    ts=turn.ts,
                    speaker=turn.speaker,
                    text=red.text,
                    state=turn.state,
                    tool=tool,
                    redaction=RedactionInfo(entities=red.entities, fallback=red.fallback),
                )
                f.write(stored.model_dump_json().encode() + b"\n")  # O_APPEND: always at the end
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return stored

    def read(self, call_id: str) -> list[TranscriptTurn]:
        """All stored turns of a call ordered by `seq`; an unknown call has none."""
        path = self._path(call_id)
        if not path.exists():
            return []
        turns = [
            TranscriptTurn.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return sorted(turns, key=lambda t: t.seq)
