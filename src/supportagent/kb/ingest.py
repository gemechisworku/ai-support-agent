"""Versioned, idempotent ingestion of policy documents into the `kb` database (KB-R5, KB-R2).

    uv run python -m supportagent.kb.ingest [PATH=data/policies] [--embedder ollama|fake] [--dry-run]
    make kb-ingest ARGS="--embedder fake"

Per document (keyed by `doc_id` + `version`):
- not in the database → **add**: chunk, embed, insert the document and its chunks in one transaction;
- same content hash and embedder → **skip** (re-running is a no-op);
- same version, different content (or a different embedder) → **conflict**: nothing is written for
  any document, and the error names the document. Bump `version` in the front-matter to change a
  published policy; old versions are kept for audit.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from supportagent.kb.chunker import ChunkDraft, chunk
from supportagent.kb.embed import Embedder, HashEmbedder, OllamaEmbedder
from supportagent.kb.frontmatter import FrontMatterError, PolicyDoc, load, load_corpus
from supportagent.kb.models import Chunk, Document

DEFAULT_PATH = Path("data/policies")
Action = Literal["add", "skip"]


class IngestConflict(RuntimeError):
    """A document's version already exists with different content or embedder."""


@dataclass(frozen=True)
class PlannedDoc:
    doc: PolicyDoc
    action: Action
    content_hash: str
    drafts: list[ChunkDraft]

    @property
    def key(self) -> str:
        return f"{self.doc.meta.doc_id}@{self.doc.meta.version}"


@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    chunks: int = 0
    dry_run: bool = False

    def summary(self) -> str:
        prefix = "dry run: would add" if self.dry_run else "added"
        return f"{prefix} {len(self.added)} / skipped {len(self.skipped)} / chunks {self.chunks}"


def content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> list[PolicyDoc]:
    return load_corpus(path) if path.is_dir() else [load(path)]


def embedding_text(draft: ChunkDraft) -> str:
    """What gets embedded for a chunk: its section path, then its text (the embedder adds any prefix)."""
    return f"{draft.section_path}\n{draft.text}"


def plan(path: Path, session: Session, embedder: Embedder) -> list[PlannedDoc]:
    """Decide add/skip for every document; raise `IngestConflict` listing every conflicting document."""
    planned: list[PlannedDoc] = []
    conflicts: list[str] = []
    for doc in _load(path):
        assert doc.path is not None
        digest = content_hash(doc.path)
        meta = doc.meta
        existing = session.scalar(
            select(Document).where(Document.doc_id == meta.doc_id, Document.version == meta.version)
        )
        if existing is None:
            planned.append(PlannedDoc(doc, "add", digest, chunk(doc.body, meta)))
        elif existing.content_hash != digest:
            conflicts.append(
                f"{meta.doc_id}@{meta.version}: content changed but version did not — bump `version` "
                f"in {doc.path}"
            )
        elif existing.embedder != embedder.name:
            conflicts.append(
                f"{meta.doc_id}@{meta.version}: already embedded with {existing.embedder}, not "
                f"{embedder.name} — vectors from different models can't be mixed (reset the kb database)"
            )
        else:
            planned.append(PlannedDoc(doc, "skip", digest, []))
    if conflicts:
        raise IngestConflict("; ".join(conflicts))
    return planned


def ingest(path: Path, session: Session, embedder: Embedder, *, dry_run: bool = False) -> IngestReport:
    """Ingest a policy file or directory. Each added document is committed in its own transaction."""
    report = IngestReport(dry_run=dry_run)
    for item in plan(path, session, embedder):
        if item.action == "skip":
            report.skipped.append(item.key)
            continue
        report.added.append(item.key)
        report.chunks += len(item.drafts)
        if dry_run:
            continue
        meta = item.doc.meta
        vectors = embedder.embed_documents([embedding_text(d) for d in item.drafts])
        document = Document(
            doc_id=meta.doc_id,
            version=meta.version,
            title=meta.title,
            product=meta.product,
            audience=meta.audience,
            effective_date=meta.effective_date,
            content_hash=item.content_hash,
            source_path=str(item.doc.path),
            embedder=embedder.name,
            chunks=[
                Chunk(
                    ord=d.ord,
                    section_path=d.section_path,
                    text=d.text,
                    token_count=d.token_count,
                    embedding=vec,
                )
                for d, vec in zip(item.drafts, vectors, strict=True)
            ],
        )
        try:
            session.add(document)
            session.commit()
        except Exception:
            session.rollback()
            raise
    return report


def _embedder(kind: str | None) -> Embedder:
    from supportagent.common.settings import get_settings
    from supportagent.kb.embed import get_embedder

    if kind == "fake":
        return HashEmbedder()
    settings = get_settings()
    if kind == "ollama":
        return OllamaEmbedder(settings.models.ollama_url, settings.models.embed_model)
    return get_embedder(settings)


def main(argv: Sequence[str] | None = None, *, session: Session | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--embedder", choices=["ollama", "fake"], help="default: KB_EMBEDDER")
    ap.add_argument("--dry-run", action="store_true", help="list planned actions; write nothing")
    args = ap.parse_args(argv)

    embedder = _embedder(args.embedder)
    own_session = session is None
    if session is None:
        from supportagent.common.db import kb_engine

        session = Session(kb_engine(), expire_on_commit=False)
    try:
        report = ingest(args.path, session, embedder, dry_run=args.dry_run)
    except (IngestConflict, FrontMatterError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if own_session:
            session.close()
    if args.dry_run:
        for key in report.added:
            print(f"add   {key}")
        for key in report.skipped:
            print(f"skip  {key}")
    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
