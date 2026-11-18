"""A tool call the model wrote as plain text must never be spoken (T-AGENT-02).

Llama 3.1 8B behind `llama-server --jinja` sometimes answers with `{"name": "finish", "parameters": {}}`
as the message *content* instead of a tool call. Unguarded, that text is sent to TTS, shown in the chat and
recorded as something the agent said (found on the second playground call).

`guard_tool_json` wraps the model's chunk stream. Content that starts like a JSON object is held back until it
is complete. If the whole message is exactly one JSON object with a `name` and `parameters` (or `arguments`),
it is never emitted as text: when the tool is in this state's allow-list it becomes a real tool call (so
LiveKit validates and runs it exactly as it would any other); otherwise it is dropped and only the tool *name*
is logged. Any other text, including text that merely begins with a brace, is released unchanged.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Callable, Collection
from typing import Any

from livekit.agents import llm

from supportagent.common.logging import get_logger

log = get_logger(__name__)

_MAX_HELD_CHARS = 2000  # a longer "JSON" is not a tool call: release it


def parse_tool_json(text: str) -> tuple[str, dict[str, Any]] | None:
    """`(name, arguments)` when the whole text is one tool-call object, else `None`."""
    try:
        data = json.loads(text.strip())
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    args = data.get("parameters", data.get("arguments", {}))
    if not isinstance(name, str) or not name or not isinstance(args, dict):
        return None
    if set(data) - {"name", "parameters", "arguments", "type"}:
        return None
    return name, args


def _text_of(chunk: llm.ChatChunk | str) -> str | None:
    """The text a chunk carries, or `None` when it carries a tool call or nothing."""
    if isinstance(chunk, str):
        return chunk
    delta = chunk.delta
    if delta is None or delta.tool_calls:
        return None
    return delta.content


async def guard_tool_json(stream: AsyncIterable[Any], allowed_tools: Collection[str]) -> AsyncIterator[Any]:
    held: list[Any] = []
    text = ""
    holding = False
    async for chunk in stream:
        piece = _text_of(chunk)
        if piece is None:
            if holding:  # a real tool call arrived: what we held was text after all
                for h in held:
                    yield h
                held, text, holding = [], "", False
            yield chunk
            continue
        if not holding:
            if not text and not piece.strip():
                yield chunk  # leading whitespace: nothing to decide yet
                continue
            if not (text + piece).lstrip().startswith("{"):
                yield chunk
                continue
            holding = True
        held.append(chunk)
        text += piece
        if len(text) > _MAX_HELD_CHARS:
            for h in held:
                yield h
            held, text, holding = [], "", False
    if not holding:
        return
    parsed = parse_tool_json(text)
    if parsed is None:
        for h in held:
            yield h
        return
    name, args = parsed
    if name not in allowed_tools:
        log.warning("dropped a tool call written as text: the tool is not offered here", extra={"tool": name})
        return
    log.warning("a tool call written as text was run as a tool call", extra={"tool": name})
    call_id = f"textcall-{uuid.uuid4().hex[:8]}"
    yield llm.ChatChunk(
        id=call_id,
        delta=llm.ChoiceDelta(
            role="assistant",
            tool_calls=[llm.FunctionToolCall(name=name, arguments=json.dumps(args), call_id=call_id)],
        ),
    )


# --------------------------------------------------------------------------- recited prompt examples


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", "", text.lower().replace("'", "")).split())


async def drop_recited_fragments(
    stream: AsyncIterable[Any], fragments: Collection[str], on_drop: Callable[[], object] | None = None
) -> AsyncIterator[Any]:
    """Never speak a reply that is exactly one of `fragments` (a phrase the prompt quoted as an example).

    The 8B model answered the router with "That's all." because the prompt quoted it as something a *caller*
    says (found on the third playground call). Text that could still become one of the fragments is held
    back; as soon as it cannot, or a tool call arrives, it is released unchanged. A message that ends as
    exactly a fragment is dropped (punctuation and case ignored) and `on_drop` is called, so the state can
    say something useful in code instead.
    """
    banned = {_normalise(f) for f in fragments}
    held: list[Any] = []
    text = ""
    async for chunk in stream:
        piece = _text_of(chunk)
        if piece is None:
            for h in held:
                yield h
            held, text = [], ""
            yield chunk
            continue
        held.append(chunk)
        text += piece
        norm = _normalise(text)
        if norm and not any(b.startswith(norm) for b in banned):
            for h in held:
                yield h
            held, text = [], ""
            # Once released, later chunks of this message pass straight through (the prefix cannot recover).
            async for rest in stream:
                yield rest
            return
    if not held:
        return
    if _normalise(text) in banned:
        log.warning("dropped a reply that recited a prompt example")
        if on_drop is not None:
            on_drop()
        return
    for h in held:
        yield h
