"""Heading-based Markdown chunker for policy documents (KB-R3). Pure: no I/O, deterministic.

    chunk(markdown, meta) -> list[ChunkDraft]

1. Front-matter (a leading `---` block) is ignored; ingestion parses it separately (T-KB-01).
2. The body is split into sections at Markdown headings (not inside code fences). A section's
   `section_path` is its heading trail joined with `" > "` (`"Late fees > Auto loan"`); text before
   the first heading uses the document title.
3. Each section is split into blocks: paragraphs, tables, list items and code fences. Tables, list
   items and code fences are atomic and never split; an over-long paragraph is split at sentence ends.
4. Short sibling sections (same parent) are merged up to ~`TARGET_TOKENS`; the merged chunk keeps
   each section's heading line in its text and takes the parent's path.
5. Long sections are split on block boundaries into balanced pieces of about 300-500 tokens; no chunk
   exceeds `MAX_TOKENS`. An atomic block larger than that is a corpus error (`ValueError`).

Tokens are estimated as `ceil(len(text) / 4)` (plan: no tokenizer dependency).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal, Protocol

MIN_TOKENS = 300
TARGET_TOKENS = 500
MAX_TOKENS = 600

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

BlockKind = Literal["paragraph", "table", "list_item", "code", "sentence"]
_ATOMIC: frozenset[BlockKind] = frozenset({"table", "list_item", "code"})


class DocMeta(Protocol):
    """What the chunker needs from a document's front-matter (satisfied by `PolicyDocMeta`)."""

    @property
    def doc_id(self) -> str: ...

    @property
    def title(self) -> str: ...


@dataclass(frozen=True)
class ChunkDraft:
    """A chunk before embedding: maps to a `kb.chunks` row (T-KB-02) without document id/embedding."""

    ord: int
    section_path: str
    text: str
    token_count: int


@dataclass(frozen=True)
class _Block:
    kind: BlockKind
    text: str

    @property
    def tokens(self) -> int:
        return approx_tokens(self.text)


@dataclass
class _Section:
    level: int  # 0 for text before the first heading
    titles: list[str]  # heading trail, outermost first
    blocks: list[_Block]

    @property
    def parent(self) -> tuple[str, ...]:
        return tuple(self.titles[:-1])

    @property
    def tokens(self) -> int:
        return approx_tokens(_join(self.blocks))

    def heading_line(self) -> str:
        return f"{'#' * self.level} {self.titles[-1]}"


def approx_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def chunk(markdown: str, meta: DocMeta) -> list[ChunkDraft]:
    """Split one policy document into ordered chunks. Raises `ValueError` for an atomic block > MAX."""
    sections = [s for s in _sections(_strip_front_matter(markdown)) if s.blocks]
    pieces: list[tuple[str, str]] = []  # (section_path, text)
    group: list[_Section] = []

    def flush_group() -> None:
        if len(group) == 1:
            pieces.extend(_split_section(group[0], meta))
        elif group:
            text = "\n\n".join(f"{s.heading_line()}\n\n{_join(s.blocks)}" for s in group)
            pieces.append((_path(list(group[0].parent), meta), text))
        group.clear()

    for section in sections:
        if group and _can_merge(group, section):
            group.append(section)
            continue
        flush_group()
        group.append(section)
    flush_group()
    return [ChunkDraft(i, path, text, approx_tokens(text)) for i, (path, text) in enumerate(pieces)]


# --------------------------------------------------------------------------- parsing


def _strip_front_matter(markdown: str) -> str:
    lines = markdown.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1 :])
    return markdown


def _sections(body: str) -> list[_Section]:
    stack: list[tuple[int, str]] = []
    sections = [_Section(0, [], [])]
    lines: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m:
            sections[-1].blocks = _blocks(lines)
            lines = []
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            sections.append(_Section(level, [t for _, t in stack], []))
        else:
            lines.append(line)
    sections[-1].blocks = _blocks(lines)
    return sections


def _blocks(lines: list[str]) -> list[_Block]:
    blocks: list[_Block] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if _FENCE.match(line):
            j = i + 1
            while j < len(lines) and not _FENCE.match(lines[j]):
                j += 1
            j = min(j + 1, len(lines))
            blocks.append(_Block("code", "\n".join(lines[i:j]).rstrip()))
        elif line.lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            blocks.append(_Block("table", "\n".join(lines[i:j]).rstrip()))
        elif _LIST_ITEM.match(line):
            j = i + 1
            while j < len(lines) and lines[j].strip() and not _starts_block(lines[j]):
                j += 1
            blocks.append(_Block("list_item", "\n".join(lines[i:j]).rstrip()))
        else:
            j = i + 1
            while j < len(lines) and lines[j].strip() and not _starts_block(lines[j]):
                j += 1
            blocks.append(_Block("paragraph", "\n".join(lines[i:j]).strip()))
        i = j
    return blocks


def _starts_block(line: str) -> bool:
    return bool(_LIST_ITEM.match(line) or _FENCE.match(line) or line.lstrip().startswith("|"))


# --------------------------------------------------------------------------- packing


def _join(blocks: list[_Block]) -> str:
    out = ""
    for prev, block in zip([None, *blocks[:-1]], blocks, strict=True):
        if prev is None:
            sep = ""
        elif prev.kind == block.kind == "list_item":
            sep = "\n"
        elif prev.kind == block.kind == "sentence":
            sep = " "
        else:
            sep = "\n\n"
        out += sep + block.text
    return out


def _path(titles: list[str], meta: DocMeta) -> str:
    return " > ".join(titles) if titles else meta.title


def _can_merge(group: list[_Section], section: _Section) -> bool:
    if section.level == 0 or any(s.level == 0 for s in group) or section.parent != group[0].parent:
        return False
    group_tokens = sum(s.tokens for s in group)
    if group_tokens + section.tokens > TARGET_TOKENS:
        return False
    return group_tokens < MIN_TOKENS or section.tokens < MIN_TOKENS


def _split_section(section: _Section, meta: DocMeta) -> list[tuple[str, str]]:
    path = _path(section.titles, meta)
    blocks: list[_Block] = []
    for block in section.blocks:
        if block.tokens <= MAX_TOKENS:
            blocks.append(block)
        elif block.kind in _ATOMIC:
            raise ValueError(
                f"{meta.doc_id}: {block.kind} in section '{path}' is ~{block.tokens} tokens, over the "
                f"{MAX_TOKENS}-token hard max; tables, list items and code are never split, so shorten it"
            )
        else:
            blocks.extend(_sentences(block, meta, path))
    return [(path, _join(piece)) for piece in _pack(blocks)]


def _sentences(block: _Block, meta: DocMeta, path: str) -> list[_Block]:
    out = []
    for sentence in _SENTENCE_END.split(block.text):
        if approx_tokens(sentence) > MAX_TOKENS:
            raise ValueError(f"{meta.doc_id}: a sentence in section '{path}' exceeds {MAX_TOKENS} tokens")
        out.append(_Block("sentence", sentence))
    return out


def _pack(blocks: list[_Block]) -> list[list[_Block]]:
    """Greedy, balanced packing on block boundaries: about total/n tokens per piece, never > MAX."""
    total = approx_tokens(_join(blocks))
    if total <= TARGET_TOKENS:
        return [blocks]
    goal = total / math.ceil(total / TARGET_TOKENS)
    pieces: list[list[_Block]] = []
    current: list[_Block] = []
    for block in blocks:
        if current:
            now = approx_tokens(_join(current))
            after = approx_tokens(_join([*current, block]))
            if after > MAX_TOKENS or abs(after - goal) > abs(now - goal):
                pieces.append(current)
                current = []
        current.append(block)
    pieces.append(current)
    if len(pieces) > 1 and approx_tokens(_join(pieces[-1])) < MIN_TOKENS:
        merged = [*pieces[-2], *pieces[-1]]
        if approx_tokens(_join(merged)) <= MAX_TOKENS:
            pieces[-2:] = [merged]
    return pieces
