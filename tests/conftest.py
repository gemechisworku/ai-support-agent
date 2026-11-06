"""Shared pytest fixtures (T-PLAT-02, T-PLAT-05).

Unit:
- `settings`: Settings from the environment only (local .env ignored), required secrets filled in.
- `frozen_clock`: `supportagent.common.clock` frozen at 2026-11-09T14:00Z (a Monday; the persona
  scenarios' "as of" date). Use `frozen_clock.advance(...)` / `.set(...)`.

Integration (`@pytest.mark.integration`, needs `make up`):
- `db_settings` (session): Settings pointed at the `bank_test` / `kb_test` databases, which are
  migrated to head once per session.
- `bank_db` / `kb_db`: a SQLAlchemy `Session` inside a transaction that is rolled back after the
  test. `session.commit()` in code under test only releases a SAVEPOINT, so nothing leaks between
  tests. Pass this session to the code under test instead of using `db.bank_engine()`.

Without Postgres, integration fixtures skip with a hint locally and fail when `CI` is set.
These fixtures never change process-wide environment variables or cached settings outside the
migration step, so unit tests that run later are unaffected.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from supportagent.common import db, migrate
from supportagent.common import settings as settings_module
from supportagent.common.clock import FrozenClock, freeze_clock
from supportagent.common.settings import Settings, get_settings, load_settings

# Values for required secrets so tests never depend on a developer's local .env.
TEST_ENV = {
    "POSTGRES_PASSWORD": "test-only",
    "GATEWAY_SERVICE_TOKEN": "test-service-token",
    "GATEWAY_WORKER_TOKEN": "test-worker-token",
    "AUDIT_HASH_SALT": "test-salt",
    "SCHEDULER_TOKEN": "test-scheduler-token",
}
# Integration tests keep the real POSTGRES_* from the environment (CI job / docker run -e);
# otherwise they use the compose defaults from .env.example.
DB_DEFAULTS = {**TEST_ENV, "POSTGRES_PASSWORD": "change-me-local-only"}
TEST_DATABASES = {"BANK_DB": "bank_test", "KB_DB": "kb_test"}
FROZEN_AT = "2026-11-09T14:00:00Z"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Settings built from the environment only (the local .env is ignored), cache cleared around the test."""
    monkeypatch.setattr(settings_module, "ENV_FILE", None)
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def frozen_clock() -> Iterator[FrozenClock]:
    with freeze_clock(FROZEN_AT) as fc:
        yield fc


# --------------------------------------------------------------------------- integration


def require_postgres(engine: Engine) -> None:
    """Skip the test with a hint if Postgres is unreachable; fail instead when running in CI."""
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except OperationalError as exc:
        reason = f"Postgres not reachable at {engine.url.host}:{engine.url.port} ({exc.__class__.__name__})"
        if os.environ.get("CI"):
            pytest.fail(f"{reason}; integration tests must run in CI")
        pytest.skip(f"{reason}; run `make up`")


@pytest.fixture(scope="session")
def db_settings() -> Iterator[Settings]:
    """Settings for the *_test databases, migrated to head once per test session."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings_module, "ENV_FILE", None)
        for key, value in DB_DEFAULTS.items():
            if not os.environ.get(key):
                mp.setenv(key, value)
        for key, value in TEST_DATABASES.items():
            mp.setenv(key, value)
        get_settings.cache_clear()
        db.reset_engines()
        try:
            test_settings = load_settings(None)
            require_postgres(db.make_engine(test_settings.postgres.dsn("bank")))
            migrate.upgrade("bank")  # Alembic env reads get_settings(), patched above
            migrate.upgrade("kb")
        finally:
            db.reset_engines()
            get_settings.cache_clear()
    yield test_settings


def _rollback_session(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture(scope="session")
def _bank_engine(db_settings: Settings) -> Iterator[Engine]:
    engine = db.make_engine(db_settings.postgres.dsn("bank"))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def _kb_engine(db_settings: Settings) -> Iterator[Engine]:
    engine = db.make_engine(db_settings.postgres.dsn("kb"))
    yield engine
    engine.dispose()


@pytest.fixture
def bank_db(_bank_engine: Engine) -> Iterator[Session]:
    yield from _rollback_session(_bank_engine)


@pytest.fixture
def kb_db(_kb_engine: Engine) -> Iterator[Session]:
    yield from _rollback_session(_kb_engine)
