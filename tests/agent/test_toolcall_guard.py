# ruff: noqa: F811
# (the router tests' fixtures are imported by name and then used as test parameters)
"""T-AGENT-02: a tool call the model wrote as plain text is never spoken or recorded.

Found on the second playground call: the agent "said" `{"name": "finish", "parameters": {}}`.
"""

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from livekit.agents import llm

from supportagent.agent.testing import say
from supportagent.agent.toolcall_guard import drop_recited_fragments, guard_tool_json, parse_tool_json
from tests.agent.test_router import _agent_lines, _run, prompts, stand_ins  # noqa: F401  (fixtures)

ALLOWED = {"finish", "route_policy_question"}


def _text(piece: str) -> llm.ChatChunk:
    return llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", content=piece))


def _call(name: str) -> llm.ChatChunk:
    call = llm.FunctionToolCall(name=name, arguments="{}", call_id="real")
    return llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", tool_calls=[call]))


async def _stream(items: Sequence[Any]) -> AsyncIterator[Any]:
    for item in items:
        yield item


async def _guard(items: Sequence[Any], allowed: set[str] = ALLOWED) -> list[Any]:
    return [c async for c in guard_tool_json(_stream(items), allowed)]


def _contents(chunks: list[Any]) -> str:
    return "".join(c if isinstance(c, str) else (c.delta.content or "") for c in chunks if c is not None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"name": "finish", "parameters": {}}', ("finish", {})),
        ('  {"name": "finish", "arguments": {"a": 1}}  ', ("finish", {"a": 1})),
        ('{"name": "finish"}', ("finish", {})),
        ("Hello there", None),
        ('{"name": "finish", "parameters": {}} and more', None),
        ('{"hello": "world"}', None),
        ('{"name": "", "parameters": {}}', None),
        ('{"name": "finish", "parameters": "x"}', None),
        ('{"name": "finish", "parameters": {}, "extra": 1}', None),
        ("[1, 2]", None),
    ],
)
def test_parse_tool_json(text: str, expected: tuple[str, dict[str, Any]] | None) -> None:
    assert parse_tool_json(text) == expected


async def test_ordinary_text_passes_through_unchanged() -> None:
    chunks = [_text("Hello, "), _text("how can I help?")]
    assert await _guard(chunks) == chunks


async def test_a_tool_call_written_as_text_is_never_emitted_as_text() -> None:
    out = await _guard([_text('{"name": "fin'), _text('ish", "parameters": {}}')])
    assert _contents(out) == ""  # nothing to speak or record
    [call] = [c.delta.tool_calls[0] for c in out]
    assert (call.name, json.loads(call.arguments)) == ("finish", {})  # run as a real tool call


async def test_a_tool_not_offered_here_is_dropped_not_run(caplog: pytest.LogCaptureFixture) -> None:
    secret = '{"name": "get_loan_status", "parameters": {"loan_last4": "4417"}}'
    with caplog.at_level(logging.WARNING):
        out = await _guard([_text(secret)])
    assert out == []
    logged = " ".join(r.getMessage() + str(getattr(r, "tool", "")) for r in caplog.records)
    assert "get_loan_status" in logged  # the tool name is logged
    assert "4417" not in logged  # the arguments never are


async def test_a_str_chunk_stream_is_guarded_too() -> None:
    out = await _guard(['{"name": "finish", ', '"parameters": {}}'])
    assert _contents([c for c in out if isinstance(c, str)]) == ""
    assert len(out) == 1


async def test_text_that_only_starts_with_a_brace_is_released() -> None:
    chunks = [_text("{Sorry} "), _text("about that.")]
    assert _contents(await _guard(chunks)) == "{Sorry} about that."


async def test_a_real_tool_call_after_held_text_releases_the_text_first() -> None:
    chunks = [_text("{"), _call("finish")]
    out = await _guard(chunks)
    assert out == chunks


async def test_leading_whitespace_does_not_hide_ordinary_text() -> None:
    chunks = [_text(" "), _text("Sure.")]
    assert _contents(await _guard(chunks)) == " Sure."


async def test_a_long_brace_text_is_released_not_held_forever() -> None:
    big = "{" + "x" * 2500
    out = await _guard([_text(big)])
    assert _contents(out) == big


# --- through a real state, in text mode


async def test_a_text_tool_call_in_the_router_runs_the_tool_and_is_not_said(
    prompts: Any,
    stand_ins: None,
) -> None:
    steps = [say('{"name": "route_policy_question", "parameters": {}}'), say("unused")]
    result = await _run(prompts, steps[:1], ["Can I pay early?"])
    said = " ".join(_agent_lines(result))
    assert "{" not in said and "route_policy_question" not in said
    assert "policy" in result.states  # the tool ran: the call moved to the policy state


async def test_a_text_tool_call_for_a_tool_the_state_lacks_is_neither_said_nor_run(
    prompts: Any,
    stand_ins: None,
) -> None:
    result = await _run(prompts, [say('{"name": "get_loan_status", "parameters": {}}')], ["Can I pay early?"])
    said = " ".join(_agent_lines(result))
    assert "{" not in said and "get_loan_status" not in said
    assert "get_loan_status" not in result.tool_names


# ------------------------------------------------------------------ recited prompt examples (third real call)

FRAGMENTS = ("that's all", "no thanks")


async def _drop(items: Sequence[Any]) -> tuple[list[Any], list[str]]:
    dropped: list[str] = []
    out = [c async for c in drop_recited_fragments(_stream(items), FRAGMENTS, lambda: dropped.append("x"))]
    return out, dropped


@pytest.mark.parametrize(
    "pieces", [["That's all."], ["That", "'s ", "all", "."], ["no thanks"], [" No thanks! "]]
)
async def test_a_reply_that_is_exactly_a_quoted_example_is_dropped(pieces: list[str]) -> None:
    out, dropped = await _drop([_text(p) for p in pieces])
    assert _contents(out) == ""
    assert dropped == ["x"]


@pytest.mark.parametrize(
    "pieces",
    [
        ["That's all I can say about fees."],
        ["Is there anything else?"],
        ["That", "'s a good question."],
        ["No", " thanks are needed, the late fee is $25."],
    ],
)
async def test_a_longer_reply_that_starts_like_an_example_is_released_unchanged(pieces: list[str]) -> None:
    out, dropped = await _drop([_text(p) for p in pieces])
    assert _contents(out) == "".join(pieces)
    assert dropped == []


async def test_a_tool_call_releases_what_was_held() -> None:
    call = _call("finish")
    out, dropped = await _drop([_text("That"), call])
    assert _contents(out) == "That"
    assert out[-1] is call
    assert dropped == []
