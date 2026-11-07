"""The `kb` database: versioned policy documents and their embedded chunks (KB-R2).

Every ingested version is kept (`unique(doc_id, version)`); retrieval picks the version effective at
the query's `as_of`. `chunks.tsv` is a stored generated column, so full-text search needs no
application code to stay in sync.
"""

import datetime as dt

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from supportagent.common.db import KbBase

EMBED_DIM = 768
TSV_EXPRESSION = "to_tsvector('english', coalesce(section_path, '') || ' ' || coalesce(\"text\", ''))"


class Document(KbBase):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("doc_id", "version"),
        Index("ix_documents_doc_id_effective_date", "doc_id", "effective_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    product: Mapped[str] = mapped_column(Text)  # product code or "all"
    audience: Mapped[str] = mapped_column(Text)  # customer | internal
    effective_date: Mapped[dt.date] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(Text)  # sha256 of the source file
    source_path: Mapped[str] = mapped_column(Text)
    embedder: Mapped[str] = mapped_column(Text)  # Embedder.name, e.g. "hash-v1"
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Chunk.ord"
    )


class Chunk(KbBase):
    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("chunks_tsv_gin", "tsv", postgresql_using="gin"),
        Index("ix_chunks_document_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    ord: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed(TSV_EXPRESSION, persisted=True))

    document: Mapped[Document] = relationship(back_populates="chunks")
