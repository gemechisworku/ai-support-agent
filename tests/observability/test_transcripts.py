"""T-OBS-03 · OBS-R4: the redacted, append-only JSONL transcript store. Synthetic data only."""

import datetime as dt
import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from supportagent.contracts.transcript import RawTurn, RedactionInfo, ToolRef, TranscriptTurn
from supportagent.observability.transcripts import TranscriptStore

CALL = "fbde0dde-804f-4a79-ba80-2795d29b3079"
TS = dt.datetime(2026, 11, 9, 14, 0, tzinfo=dt.UTC)
PERSONA = "Frank Holloway"
PHONE = "+1 415 555 0134"


@pytest.fixture
def store(tmp_path: Path) -> TranscriptStore:
    return TranscriptStore(tmp_path / "transcripts")


def raw(text: str, **kw: object) -> RawTurn:
    return RawTurn(**{"speaker": "caller", "text": text, "ts": TS, **kw})  # type: ignore[arg-type]


def lines(store: TranscriptStore, call_id: str = CALL) -> list[str]:
    return (store.base_dir / f"{call_id}.jsonl").read_text().splitlines()


def test_phone_and_persona_name_are_stored_as_placeholders(store: TranscriptStore) -> None:
    turn = store.append(
        CALL, raw(f"This is {PERSONA}, call me on {PHONE}.", known_pii=[PERSONA, PHONE], state="verify")
    )
    assert PERSONA not in turn.text and PHONE not in turn.text
    assert "<PHONE>" in turn.text
    assert "PHONE" in turn.redaction.entities and turn.redaction.fallback is False
    on_disk = (store.base_dir / f"{CALL}.jsonl").read_text()
    assert PERSONA not in on_disk and "Holloway" not in on_disk and "415" not in on_disk
    assert turn.state == "verify" and turn.seq == 1 and turn.call_id == CALL


def test_known_pii_is_never_persisted(store: TranscriptStore) -> None:
    store.append(CALL, raw("hello there", known_pii=["SECRET-VALUE-123"]))
    stored = json.loads(lines(store)[0])
    assert "known_pii" not in stored
    assert "SECRET-VALUE-123" not in lines(store)[0]
    assert set(stored) == set(TranscriptTurn.model_fields)


def test_tool_args_are_redacted_and_amounts_kept(store: TranscriptStore) -> None:
    tool = ToolRef(
        name="record_promise_to_pay",
        args={"phone": PHONE, "note": [f"call {PERSONA}"], "amount": "412.50", "days": 3},
        decision="allowed",
        latency_ms=12,
    )
    turn = store.append(CALL, raw("ok", speaker="system", tool=tool, known_pii=[PERSONA]))
    assert turn.tool is not None
    assert PHONE not in json.dumps(turn.tool.args) and PERSONA not in json.dumps(turn.tool.args)
    assert turn.tool.args["amount"] == "412.50" and turn.tool.args["days"] == 3
    assert turn.tool.name == "record_promise_to_pay" and turn.tool.decision == "allowed"


def test_read_returns_turns_in_seq_order_and_round_trips(store: TranscriptStore) -> None:
    for i, who in enumerate(["agent", "caller", "agent"]):
        store.append(CALL, raw(f"turn {i}", speaker=who, ts=TS + dt.timedelta(seconds=i)))  # type: ignore[arg-type]
    turns = store.read(CALL)
    assert [t.seq for t in turns] == [1, 2, 3]
    assert [t.text for t in turns] == ["turn 0", "turn 1", "turn 2"]
    assert all(isinstance(t, TranscriptTurn) and t.ts.tzinfo is not None for t in turns)
    for line, turn in zip(lines(store), turns, strict=True):  # JSONL schema round-trip
        assert TranscriptTurn.model_validate_json(line) == turn


def test_read_of_an_unknown_call_is_empty(store: TranscriptStore) -> None:
    assert store.read(CALL) == []


def test_two_interleaved_writers_get_gapless_unique_seqs(store: TranscriptStore) -> None:
    other = TranscriptStore(store.base_dir)  # a second writer on the same directory
    errors: list[BaseException] = []

    def write(s: TranscriptStore, tag: str) -> None:
        try:
            for i in range(25):
                s.append(CALL, raw(f"{tag} {i}"))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(s, t)) for s, t in ((store, "a"), (other, "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert [t.seq for t in store.read(CALL)] == list(range(1, 51))
    assert len(lines(store)) == 50


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "a\\b", "..", ".", "", "x" * 200, "a b", "/abs"])
def test_invalid_call_ids_are_rejected(store: TranscriptStore, bad: str) -> None:
    with pytest.raises(ValueError, match="call_id"):
        store.append(bad, raw("x"))
    with pytest.raises(ValueError, match="call_id"):
        store.read(bad)
    with pytest.raises(ValueError, match="call_id"):
        store.uri(bad)
    assert not store.base_dir.exists() or list(store.base_dir.iterdir()) == []


def test_uri_is_repo_relative(store: TranscriptStore) -> None:
    assert TranscriptStore(Path("var/transcripts")).uri(CALL) == f"file://var/transcripts/{CALL}.jsonl"


def test_default_base_dir_comes_from_settings(settings: object) -> None:
    assert TranscriptStore().base_dir == Path("var/transcripts")


def test_no_public_method_takes_or_writes_unredacted_text(store: TranscriptStore) -> None:
    public = {n for n in dir(store) if not n.startswith("_")}
    assert public == {"append", "base_dir", "read", "uri"}
    # RawTurn is the only input type; an already-built TranscriptTurn is not accepted.
    stored = store.append(CALL, raw("hi"))
    with pytest.raises((TypeError, AttributeError, ValidationError)):
        store.append(CALL, stored)  # type: ignore[arg-type]


def test_a_presidio_failure_still_stores_no_raw_phone(
    store: TranscriptStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from supportagent.observability import redaction

    def boom(_text: str) -> tuple[str, list[str]]:
        raise RuntimeError("analyzer down")

    monkeypatch.setattr(redaction, "_presidio_pass", boom)
    turn = store.append(CALL, raw(f"my number is {PHONE}"))
    assert PHONE not in turn.text and turn.redaction.fallback is True
    assert "415" not in lines(store)[0]


def test_redaction_info_contract() -> None:
    info = RedactionInfo(entities=["PHONE"], fallback=False)
    assert info.entities == ["PHONE"]
    with pytest.raises(ValidationError):
        RawTurn(speaker="robot", text="x", ts=TS)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RawTurn(speaker="agent", text="x", ts=dt.datetime(2026, 11, 9, 14, 0))  # naive timestamp
