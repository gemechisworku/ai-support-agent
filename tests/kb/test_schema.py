"""T-KB-02 · KB-R2: the `kb` schema — documents, chunks, HNSW (cosine) and GIN indexes, generated tsv."""

import datetime as dt
import os
from collections.abc import Iterator

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from supportagent.common import db, migrate
from supportagent.common import settings as settings_module
from supportagent.common.settings import Settings, get_settings
from supportagent.kb.models import EMBED_DIM, Chunk, Document

pytestmark = pytest.mark.integration


def _doc(version: str = "1.0") -> Document:
    return Document(
        doc_id="late-fees-penalty-interest",
        version=version,
        title="Late fees",
        product="all",
        audience="customer",
        effective_date=dt.date(2026, 1, 1),
        content_hash="0" * 64,
        source_path="data/policies/late-fees-penalty-interest.md",
        embedder="hash-v1",
    )


def _vec() -> list[float]:
    return [1.0] + [0.0] * (EMBED_DIM - 1)


def test_indexes_use_the_right_access_methods(kb_db: Session) -> None:
    rows = kb_db.execute(
        text(
            "select c.relname, am.amname, pg_get_indexdef(c.oid) from pg_class c "
            "join pg_am am on am.oid = c.relam where c.relkind = 'i' and c.relname in "
            "('chunks_embedding_hnsw', 'chunks_tsv_gin')"
        )
    ).all()
    found = {name: (am, ddl) for name, am, ddl in rows}
    assert found["chunks_embedding_hnsw"][0] == "hnsw"
    assert "vector_cosine_ops" in found["chunks_embedding_hnsw"][1]
    assert "m='16'" in found["chunks_embedding_hnsw"][1]
    assert found["chunks_tsv_gin"][0] == "gin"


def test_embedding_is_768_dimensional(kb_db: Session) -> None:
    typ = kb_db.execute(
        text(
            "select format_type(atttypid, atttypmod) from pg_attribute "
            "where attrelid = 'chunks'::regclass and attname = 'embedding'"
        )
    ).scalar_one()
    assert typ == "vector(768)"


def test_generated_tsv_is_populated_on_insert(kb_db: Session) -> None:
    doc = _doc()
    doc.chunks.append(
        Chunk(
            ord=0,
            section_path="Late fees > Auto loan",
            text="A late fee applies after grace.",
            token_count=8,
            embedding=_vec(),
        )
    )
    kb_db.add(doc)
    kb_db.flush()
    tsv = kb_db.execute(
        text("select tsv::text from chunks where document_id = :d"), {"d": doc.id}
    ).scalar_one()
    assert "'auto'" in tsv and "'grace'" in tsv and "'late'" in tsv
    hit = kb_db.execute(
        text("select count(*) from chunks where tsv @@ websearch_to_tsquery('english', 'grace fee')")
    ).scalar_one()
    assert hit == 1


def test_unique_doc_id_version(kb_db: Session) -> None:
    kb_db.add(_doc("1.0"))
    kb_db.add(_doc("1.1"))  # a new version is fine
    kb_db.flush()
    kb_db.add(_doc("1.0"))
    with pytest.raises(IntegrityError):
        kb_db.flush()
    kb_db.rollback()


def test_deleting_a_document_deletes_its_chunks(kb_db: Session) -> None:
    doc = _doc()
    doc.chunks.append(Chunk(ord=0, section_path="s", text="t", token_count=1, embedding=_vec()))
    kb_db.add(doc)
    kb_db.flush()
    kb_db.execute(text("delete from documents where id = :d"), {"d": doc.id})
    assert kb_db.scalar(select(Chunk).where(Chunk.document_id == doc.id)) is None


@pytest.fixture
def kb_migration_env(db_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the Alembic env (which reads get_settings()) at kb_test for an upgrade/downgrade round trip."""
    monkeypatch.setattr(settings_module, "ENV_FILE", None)
    pg = db_settings.postgres
    env = {
        "POSTGRES_HOST": pg.host,
        "POSTGRES_PORT": str(pg.port),
        "POSTGRES_USER": pg.user,
        "POSTGRES_PASSWORD": pg.password.get_secret_value(),
        "BANK_DB": pg.bank_db,
        "KB_DB": pg.kb_db,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in ("GATEWAY_SERVICE_TOKEN", "GATEWAY_WORKER_TOKEN", "AUDIT_HASH_SALT", "SCHEDULER_TOKEN"):
        if not os.environ.get(key):
            monkeypatch.setenv(key, "test")
    get_settings.cache_clear()
    db.reset_engines()
    yield
    migrate.upgrade("kb")  # always leave kb_test at head for the rest of the session
    db.reset_engines()
    get_settings.cache_clear()


def test_upgrade_and_downgrade(kb_migration_env: None) -> None:
    def tables() -> set[str]:
        return set(inspect(db.kb_engine()).get_table_names())

    migrate.downgrade("kb", "kb_0001")
    db.reset_engines()
    assert not {"documents", "chunks"} & tables()
    migrate.upgrade("kb")
    db.reset_engines()
    assert {"documents", "chunks"} <= tables()
    migrate.upgrade("kb")  # idempotent at head
    assert migrate.current_revision("kb") == "kb_0002"
