"""documents and chunks: 768-d vectors (HNSW, cosine) and a generated tsvector (GIN). T-KB-02.

Revision ID: kb_0002
Revises: kb_0001
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

revision: str = "kb_0002"
down_revision: str | None = "kb_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TSV_EXPRESSION = "to_tsvector('english', coalesce(section_path, '') || ' ' || coalesce(\"text\", ''))"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("doc_id", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("product", sa.Text, nullable=False),
        sa.Column("audience", sa.Text, nullable=False),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("embedder", sa.Text, nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("doc_id", "version", name="uq_documents_doc_id"),
    )
    op.create_index("ix_documents_doc_id_effective_date", "documents", ["doc_id", "effective_date"])
    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.BigInteger, nullable=False),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("section_path", sa.Text, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("token_count", sa.Integer, nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("tsv", TSVECTOR, sa.Computed(TSV_EXPRESSION, persisted=True)),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_chunks_document_id_documents", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index(
        "chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("chunks_tsv_gin", "chunks", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    # Keep the `vector` extension: it is created by infra/postgres/init and may be shared.
    op.drop_table("chunks")
    op.drop_table("documents")
