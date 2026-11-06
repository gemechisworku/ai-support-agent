"""T-KB-03 · KB-R3: heading-based Markdown chunker (pure, deterministic)."""

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from supportagent.kb.chunker import MAX_TOKENS, MIN_TOKENS, TARGET_TOKENS, ChunkDraft, approx_tokens, chunk

ROOT = Path(__file__).resolve().parents[2]
POLICIES = ROOT / "data" / "policies"


@dataclass(frozen=True)
class Meta:
    doc_id: str
    title: str


META = Meta(doc_id="late-fees", title="Late fees")


def para(words: int, word: str = "lorem") -> str:
    """A paragraph of roughly `words * (len(word)+1) / 4` tokens."""
    return " ".join([word] * words) + "."


def table(rows: int) -> str:
    lines = ["| Product | Grace days | Late fee |", "|---|---|---|"]
    lines += [
        f"| Product {i} | {i % 10} | {i * 5}.00 USD flat fee per missed instalment |" for i in range(rows)
    ]
    return "\n".join(lines)


def test_section_paths_reflect_nesting() -> None:
    md = (
        "# Late fees\n\n"
        "## Personal loan\n\n" + para(400) + "\n\n"
        "## Auto loan\n\n" + para(400) + "\n\n"
        "### Commercial vehicles\n\n" + para(400) + "\n"
    )
    paths = [c.section_path for c in chunk(md, META)]
    assert paths == [
        "Late fees > Personal loan",
        "Late fees > Auto loan",
        "Late fees > Auto loan > Commercial vehicles",
    ]


def test_text_before_first_heading_uses_document_title() -> None:
    (only,) = chunk("Intro text that has no heading.\n", META)
    assert only.section_path == "Late fees"
    assert only.text == "Intro text that has no heading."


def test_front_matter_is_ignored() -> None:
    md = "---\ndoc_id: late-fees\ntitle: Late fees\n---\n# Late fees\n\nBody.\n"
    (only,) = chunk(md, META)
    assert only.text == "Body."
    assert "doc_id" not in only.text


def test_short_sibling_sections_are_merged_with_their_headings() -> None:
    md = "# Late fees\n\n## Personal loan\n\nFee is 25.00.\n\n## Auto loan\n\nFee is 35.00.\n"
    (only,) = chunk(md, META)
    assert only.section_path == "Late fees"  # common parent of the merged siblings
    assert only.text == "## Personal loan\n\nFee is 25.00.\n\n## Auto loan\n\nFee is 35.00."


def test_siblings_are_not_merged_past_the_target() -> None:
    md = "# Fees\n\n## A\n\n" + para(250) + "\n\n## B\n\n" + para(250) + "\n"
    drafts = chunk(md, META)
    assert [d.section_path for d in drafts] == ["Fees > A", "Fees > B"]


def test_sections_with_different_parents_are_not_merged() -> None:
    md = "# Doc\n\n## A\n\n### A1\n\nShort a1.\n\n## B\n\n### B1\n\nShort b1.\n"
    assert [d.section_path for d in chunk(md, META)] == ["Doc > A > A1", "Doc > B > B1"]


def test_long_section_is_split_on_paragraph_boundaries() -> None:
    paragraphs = [para(120, f"w{i}") for i in range(12)]  # ~12 x 120 tokens
    md = "# Late fees\n\n## Schedule\n\n" + "\n\n".join(paragraphs) + "\n"
    drafts = chunk(md, META)
    assert len(drafts) > 1
    assert all(d.section_path == "Late fees > Schedule" for d in drafts)
    assert all(MIN_TOKENS <= d.token_count <= MAX_TOKENS for d in drafts)
    # no paragraph is cut: rejoining the chunks gives back the original paragraphs in order
    assert "\n\n".join(d.text for d in drafts).split("\n\n") == paragraphs


def test_oversized_paragraph_is_split_on_sentences() -> None:
    sentences = [f"Sentence number {i} explains a rule about late payments in detail." for i in range(200)]
    md = "# Rules\n\n" + " ".join(sentences) + "\n"
    drafts = chunk(md, META)
    assert len(drafts) > 1
    assert all(d.token_count <= MAX_TOKENS for d in drafts)
    assert all(d.text.endswith(".") for d in drafts)  # cut at sentence ends


def test_table_is_kept_whole_in_exactly_one_chunk() -> None:
    tbl = table(12)
    md = "# Fees\n\n## Schedule\n\n" + para(300) + "\n\n" + tbl + "\n\n" + para(300) + "\n"
    drafts = chunk(md, META)
    holders = [d for d in drafts if "| Product 0 |" in d.text]
    assert len(holders) == 1
    assert tbl in holders[0].text
    assert all(d.token_count <= MAX_TOKENS for d in drafts)


def test_list_items_are_never_split() -> None:
    items = [f"- Item {i}: " + " ".join(["detail"] * 60) for i in range(15)]
    items[3] += "\n  continued on an indented line"
    md = "# Channels\n\n" + "\n".join(items) + "\n"
    drafts = chunk(md, META)
    assert len(drafts) > 1
    text = "\n".join(d.text for d in drafts)
    for item in items:
        assert sum(item in d.text for d in drafts) == 1, item
    assert text.count("- Item ") == 15


def test_headings_inside_code_fences_are_not_sections() -> None:
    md = "# Doc\n\n```\n# not a heading\n```\n"
    (only,) = chunk(md, META)
    assert only.section_path == "Doc"
    assert "# not a heading" in only.text


def test_atomic_block_over_hard_max_is_an_error() -> None:
    md = "# Fees\n\n" + table(120) + "\n"
    with pytest.raises(ValueError, match="late-fees"):
        chunk(md, META)


def test_output_is_deterministic_and_ordered() -> None:
    md = "# A\n\n" + para(500, "x") + "\n\n## B\n\n" + para(500, "y") + "\n\n## C\n\nshort\n"
    first, second = chunk(md, META), chunk(md, META)
    assert first == second
    assert [d.ord for d in first] == list(range(len(first)))
    assert first[0].text.startswith("x x")
    assert all(isinstance(d, ChunkDraft) for d in first)


def test_empty_sections_produce_no_chunks() -> None:
    assert chunk("# Title only\n\n## Also empty\n", META) == []


def test_token_estimate() -> None:
    assert approx_tokens("") == 0
    assert approx_tokens("abcd") == 1
    assert approx_tokens("abcde") == 2
    assert MIN_TOKENS < TARGET_TOKENS < MAX_TOKENS


def _corpus() -> list[Path]:
    return sorted(POLICIES.glob("*.md")) if POLICIES.is_dir() else []


@pytest.mark.skipif(not _corpus(), reason="policy corpus not present yet (T-KB-01)")
@pytest.mark.parametrize("path", _corpus(), ids=lambda p: p.stem)
def test_token_bounds_over_the_whole_corpus(path: Path) -> None:
    text = path.read_text()
    front = yaml.safe_load(text.split("---", 2)[1]) if text.startswith("---") else {}
    drafts = chunk(
        text, Meta(doc_id=str(front.get("doc_id", path.stem)), title=str(front.get("title", path.stem)))
    )
    assert drafts, "every policy document yields at least one chunk"
    assert all(0 < d.token_count <= MAX_TOKENS for d in drafts)
    assert all(d.section_path for d in drafts)
