"""Writes `SeedData` into the `bank` database and fingerprints the result."""

import hashlib

from sqlalchemy import insert, text
from sqlalchemy.orm import Session

from supportagent.bank import models
from supportagent.bank.seed.builder import SeedData

MODEL_BY_TABLE = {m.__tablename__: m for m in models.BankBase.__subclasses__()}
CHUNK = 5_000


def reset(session: Session) -> None:
    """Empty every bank table (data only; the schema stays)."""
    session.execute(text(f"TRUNCATE {', '.join(models.TABLES)} RESTART IDENTITY CASCADE"))


def write(session: Session, data: SeedData, *, reset_first: bool = False) -> dict[str, int]:
    if reset_first:
        reset(session)
    counts: dict[str, int] = {}
    for table, rows in data.tables():
        model = MODEL_BY_TABLE[table]
        for start in range(0, len(rows), CHUNK):
            session.execute(insert(model), rows[start : start + CHUNK])
        counts[table] = len(rows)
    session.flush()
    return counts


def checksum(session: Session) -> str:
    """sha256 over every row of every bank table, in a stable order."""
    digest = hashlib.sha256()
    for table in models.TABLES:
        rows = session.execute(
            text(f"SELECT coalesce(string_agg(t::text, E'\\n' ORDER BY t::text), '') FROM {table} t")  # noqa: S608
        )
        digest.update(table.encode())
        digest.update(str(rows.scalar_one()).encode())
    return digest.hexdigest()


def row_counts(session: Session) -> dict[str, int]:
    counts = {}
    for table in models.TABLES:  # table names come from models.TABLES, never from input
        counts[table] = int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608
    return counts
