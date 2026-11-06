"""Database plumbing for the two Postgres databases (PLAT-R3).

- `bank`: the mock core bank and the interaction tables (owned by specs/02-mock-bank).
- `kb`: the policy knowledge base with pgvector (owned by specs/03-knowledge-base).

Synchronous SQLAlchemy 2.0 with psycopg 3 (see plan.md, "Key decisions"). Domain models subclass
`BankBase` or `KbBase`; each base has its own metadata, which its Alembic environment targets::

    from supportagent.common.db import bank_engine, session_scope

    with session_scope(bank_engine()) as session:
        session.add(obj)  # committed on exit, rolled back on exception
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from supportagent.common.settings import get_settings

# Deterministic constraint names, so Alembic autogenerate and downgrades are stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class BankBase(DeclarativeBase):
    """Declarative base for tables in the `bank` database."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class KbBase(DeclarativeBase):
    """Declarative base for tables in the `kb` database."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def make_engine(url: str) -> Engine:
    """An engine with connection health checks; connects lazily."""
    return create_engine(url, pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def bank_engine() -> Engine:
    return make_engine(get_settings().postgres.dsn("bank"))


@lru_cache(maxsize=1)
def kb_engine() -> Engine:
    return make_engine(get_settings().postgres.dsn("kb"))


def reset_engines() -> None:
    """Dispose cached engines (tests, or after settings change)."""
    for factory in (bank_engine, kb_engine):
        if factory.cache_info().currsize:
            factory().dispose()
        factory.cache_clear()


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """A session that commits on success, rolls back on any exception, and always closes."""
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
