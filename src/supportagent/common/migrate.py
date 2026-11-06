"""Alembic helpers: one migration environment per database (`migrations/bank`, `migrations/kb`).

CLI (from the repo root)::

    uv run alembic -n bank upgrade head
    uv run alembic -n kb upgrade head      # independent of bank
    make migrate                           # both, against BANK_DB / KB_DB
    make migrate-test                      # both, against bank_test / kb_test

Python (tests, service start-up)::

    from supportagent.common import migrate
    migrate.upgrade("bank")

The target database URL always comes from `Settings.postgres.dsn()`, never from `alembic.ini`.
"""

from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, MetaData, create_engine, pool

from supportagent.common import db

Database = Literal["bank", "kb"]

# Repo root: src/supportagent/common/migrate.py → parents[3]. Holds alembic.ini and migrations/.
ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = ROOT / "alembic.ini"


def alembic_config(database: Database, ini: Path = ALEMBIC_INI) -> Config:
    """Alembic config for one database's environment (`[bank]` or `[kb]` section of alembic.ini)."""
    return Config(str(ini), ini_section=database)


def engine_for(database: Database) -> Engine:
    return db.bank_engine() if database == "bank" else db.kb_engine()


def upgrade(database: Database, revision: str = "head") -> None:
    command.upgrade(alembic_config(database), revision)


def downgrade(database: Database, revision: str) -> None:
    command.downgrade(alembic_config(database), revision)


def current_revision(database: Database) -> str | None:
    with engine_for(database).connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def run_env(database: Database, target_metadata: MetaData) -> None:
    """Body of `migrations/<database>/env.py` (online and offline modes)."""
    from alembic import context  # only importable while Alembic runs an environment

    from supportagent.common.settings import get_settings

    url = get_settings().postgres.dsn(database)
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(url, poolclass=pool.NullPool)  # no pooled connections left behind
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
