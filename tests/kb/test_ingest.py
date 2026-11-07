"""T-KB-05 · KB-R5/KB-R2: versioned, idempotent ingestion (HashEmbedder against kb_test)."""

import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from supportagent.kb import ingest as ingest_module
from supportagent.kb.embed import HashEmbedder
from supportagent.kb.ingest import IngestConflict, ingest, main
from supportagent.kb.models import Chunk, Document

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "policies"
LATE_FEES = "late-fees-penalty-interest"


def _counts(session: Session) -> tuple[int, int]:
    docs = session.scalar(select(func.count()).select_from(Document)) or 0
    chunks = session.scalar(select(func.count()).select_from(Chunk)) or 0
    return docs, chunks


def _copy_doc(tmp_path: Path, doc_id: str = LATE_FEES) -> Path:
    target = tmp_path / f"{doc_id}.md"
    shutil.copy(CORPUS / f"{doc_id}.md", target)
    return target


def test_full_corpus_ingest_and_idempotency(kb_db: Session) -> None:
    n_docs = len(list(CORPUS.glob("*.md")))
    first = ingest(CORPUS, kb_db, HashEmbedder())
    assert len(first.added) == n_docs == 20
    assert first.skipped == []
    docs, chunks = _counts(kb_db)
    assert docs == n_docs
    assert chunks == first.chunks > n_docs  # every doc has at least one chunk, most several
    ids_before = sorted(kb_db.scalars(select(Chunk.id)).all())

    second = ingest(CORPUS, kb_db, HashEmbedder())
    assert second.added == [] and len(second.skipped) == n_docs
    assert second.summary() == f"added 0 / skipped {n_docs} / chunks 0"
    assert _counts(kb_db) == (docs, chunks)
    assert sorted(kb_db.scalars(select(Chunk.id)).all()) == ids_before


def test_rows_carry_metadata_and_unit_vectors(kb_db: Session) -> None:
    ingest(CORPUS, kb_db, HashEmbedder())
    doc = kb_db.scalar(select(Document).where(Document.doc_id == "product-auto-loan"))
    assert doc is not None
    assert (doc.product, doc.audience, doc.version, doc.embedder) == ("AUTO", "customer", "1.0", "hash-v1")
    assert len(doc.content_hash) == 64
    assert [c.ord for c in doc.chunks] == list(range(len(doc.chunks)))
    first = doc.chunks[0]
    assert abs(sum(x * x for x in first.embedding) - 1.0) < 1e-6
    assert first.section_path and first.token_count > 0


def test_new_version_is_added_and_old_one_kept(kb_db: Session, tmp_path: Path) -> None:
    ingest(CORPUS, kb_db, HashEmbedder())
    v11 = _copy_doc(tmp_path)
    v11.write_text(
        v11.read_text()
        .replace('version: "1.0"', 'version: "1.1"')
        .replace("effective_date: 2026-01-01", "effective_date: 2027-01-01")
    )
    report = ingest(tmp_path, kb_db, HashEmbedder())
    assert report.added == [f"{LATE_FEES}@1.1"]
    versions = kb_db.scalars(
        select(Document.version).where(Document.doc_id == LATE_FEES).order_by(Document.version)
    )
    assert list(versions) == ["1.0", "1.1"]


def test_same_version_with_edited_content_is_a_conflict(kb_db: Session, tmp_path: Path) -> None:
    ingest(CORPUS, kb_db, HashEmbedder())
    edited = _copy_doc(tmp_path)
    edited.write_text(edited.read_text() + "\nAn extra sentence.\n")
    extra = _copy_doc(tmp_path, "faq")  # unchanged; must not be written either
    extra.write_text(extra.read_text().replace('version: "1.0"', 'version: "9.9"'))
    before = _counts(kb_db)
    with pytest.raises(IngestConflict, match=f"{LATE_FEES}@1.0: content changed"):
        ingest(tmp_path, kb_db, HashEmbedder())
    assert _counts(kb_db) == before  # nothing written for any document


def test_different_embedder_is_a_conflict(kb_db: Session, tmp_path: Path) -> None:
    doc = _copy_doc(tmp_path)
    ingest(doc, kb_db, HashEmbedder())

    class OtherEmbedder(HashEmbedder):
        name = "other-v1"

    with pytest.raises(IngestConflict, match="already embedded with hash-v1"):
        ingest(doc, kb_db, OtherEmbedder())


def test_dry_run_writes_nothing(kb_db: Session) -> None:
    report = ingest(CORPUS, kb_db, HashEmbedder(), dry_run=True)
    assert len(report.added) == 20 and report.chunks > 20
    assert report.summary().startswith("dry run: would add 20")
    assert _counts(kb_db) == (0, 0)


def test_cli_summary_dry_run_and_conflict_exit_code(
    kb_db: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(CORPUS), "--embedder", "fake", "--dry-run"], session=kb_db) == 0
    out = capsys.readouterr().out
    assert f"add   {LATE_FEES}@1.0" in out and "dry run: would add 20" in out
    assert _counts(kb_db) == (0, 0)

    assert main([str(CORPUS), "--embedder", "fake"], session=kb_db) == 0
    assert "added 20 / skipped 0" in capsys.readouterr().out
    assert main([str(CORPUS), "--embedder", "fake"], session=kb_db) == 0
    assert "added 0 / skipped 20" in capsys.readouterr().out

    edited = _copy_doc(tmp_path)
    edited.write_text(edited.read_text() + "\nChanged.\n")
    assert main([str(tmp_path), "--embedder", "fake"], session=kb_db) == 1
    assert LATE_FEES in capsys.readouterr().err


def test_embedding_text_includes_section_path() -> None:
    from supportagent.kb.chunker import ChunkDraft

    draft = ChunkDraft(ord=0, section_path="Late fees > Auto loan", text="5% capped at $50.", token_count=5)
    assert ingest_module.embedding_text(draft) == "Late fees > Auto loan\n5% capped at $50."
