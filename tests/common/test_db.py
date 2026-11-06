"""T-PLAT-03 · PLAT-R3: engines/sessions for `bank` and `kb`, one Alembic environment per database.

Unit tests need no database. Integration tests use the `bank_test` / `kb_test` databases
(created by infra/postgres/init/) and migrate them through the same helpers the app uses.
The shared, rollback-per-test DB fixtures arrive with T-PLAT-05.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from supportagent.common import db, migrate
from supportagent.common import settings as settings_module
from supportagent.common.migrate import Database
from supportagent.common.settings import Settings, get_settings
from tests.conftest import TEST_ENV

ROOT = Path(__file__).resolve().parents[2]
DATABASES: tuple[Database, ...] = ("bank", "kb")


# --------------------------------------------------------------------------- unit (no database)


def test_engines_are_cached_and_use_settings(settings: Settings) -> None:
    db.reset_engines()
    assert db.bank_engine() is db.bank_engine()
    assert db.bank_engine().url.database == "bank"
    assert db.kb_engine().url.database == "kb"
    assert db.bank_engine().url.drivername == "postgresql+psycopg"
    db.reset_engines()


def test_bases_have_separate_metadata_and_naming_convention() -> None:
    assert db.BankBase.metadata is not db.KbBase.metadata
    for base in (db.BankBase, db.KbBase):
        assert base.metadata.naming_convention["pk"] == "pk_%(table_name)s"


@pytest.mark.parametrize("database", DATABASES)
def test_alembic_environment_per_database(database: Database) -> None:
    cfg = migrate.alembic_config(database)
    script = ScriptDirectory.from_config(cfg)
    assert Path(script.dir) == ROOT / "migrations" / database
    assert (Path(script.dir) / "env.py").exists()
    assert len(script.get_heads()) == 1, "exactly one head per database"
    base = next(iter(script.walk_revisions()))
    assert base.down_revision is None or isinstance(base.down_revision, str)


def test_environments_are_independent() -> None:
    heads = {d: ScriptDirectory.from_config(migrate.alembic_config(d)).get_heads() for d in DATABASES}
    assert heads["bank"] != heads["kb"], "revision ids must not be shared between databases"


def test_make_targets_exist() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "\nmigrate:" in makefile
    assert "\nmigrate-test:" in makefile


# --------------------------------------------------------------------------- integration (Postgres)


@pytest.fixture
def test_dbs(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Settings pointed at bank_test / kb_test; skips locally (fails in CI) without Postgres.

    Unlike the unit `settings` fixture, this keeps the real POSTGRES_* values from the environment
    (CI job env or the compose defaults) and only fills in secrets that are missing.
    """
    monkeypatch.setattr(settings_module, "ENV_FILE", None)
    defaults = {**TEST_ENV, "POSTGRES_PASSWORD": "change-me-local-only"}  # compose default
    for key, value in defaults.items():
        if not os.environ.get(key):
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("BANK_DB", "bank_test")
    monkeypatch.setenv("KB_DB", "kb_test")
    get_settings.cache_clear()
    db.reset_engines()
    try:
        with db.bank_engine().connect() as conn:
            conn.execute(text("select 1"))
    except OperationalError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Postgres not reachable ({exc.__class__.__name__}); run `make up`")
    yield get_settings()
    db.reset_engines()
    get_settings.cache_clear()


def _current(database: Database) -> str | None:
    return migrate.current_revision(database)


@pytest.mark.integration
@pytest.mark.parametrize("database", DATABASES)
def test_upgrade_and_downgrade(test_dbs: Settings, database: Database) -> None:
    head = ScriptDirectory.from_config(migrate.alembic_config(database)).get_current_head()
    migrate.downgrade(database, "base")
    assert _current(database) is None
    migrate.upgrade(database)
    assert _current(database) == head
    migrate.downgrade(database, "base")
    assert _current(database) is None
    migrate.upgrade(database)  # leave the test databases at head


@pytest.mark.integration
def test_kb_upgrades_on_its_own(test_dbs: Settings) -> None:
    migrate.downgrade("bank", "base")
    migrate.downgrade("kb", "base")
    migrate.upgrade("kb")
    assert _current("kb") is not None
    assert _current("bank") is None
    migrate.upgrade("bank")


@pytest.fixture
def probe_table(test_dbs: Settings) -> Iterator[str]:
    name = "plat03_probe"
    with db.bank_engine().begin() as conn:
        conn.execute(text(f"create table if not exists {name} (id int primary key)"))
        conn.execute(text(f"truncate {name}"))
    yield name
    with db.bank_engine().begin() as conn:
        conn.execute(text(f"drop table if exists {name}"))


def _count(table: str) -> int:
    with db.bank_engine().connect() as conn:
        return int(conn.execute(text(f"select count(*) from {table}")).scalar_one())


@pytest.mark.integration
def test_session_scope_commits_on_success(probe_table: str) -> None:
    with db.session_scope(db.bank_engine()) as session:
        session.execute(text(f"insert into {probe_table} values (1)"))
    assert _count(probe_table) == 1


@pytest.mark.integration
def test_session_scope_rolls_back_on_exception(probe_table: str) -> None:
    with pytest.raises(RuntimeError, match="boom"), db.session_scope(db.bank_engine()) as session:
        session.execute(text(f"insert into {probe_table} values (2)"))
        raise RuntimeError("boom")
    assert _count(probe_table) == 0
