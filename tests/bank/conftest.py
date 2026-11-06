"""Bank test fixtures.

- `bank_env`: process settings pointed at `bank_test` (for code that calls `get_settings()` / Alembic),
  restored after the test.
- `seeded_bank` (module): `bank_test` reset and seeded (seed 42, as of 2026-11-09) and committed once
  per test module; tests then read it through the rolled-back `bank_db` session.
- `api`: a FastAPI `TestClient` for the bank API, using `bank_db`, the service token from the
  `settings` fixture and the frozen clock (2026-11-09). `api.headers` already carries the token.
"""

import datetime as dt
import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from supportagent.bank.api import create_app
from supportagent.bank.api.deps import get_session
from supportagent.bank.seed import generate
from supportagent.bank.seed.writer import write
from supportagent.common import db
from supportagent.common import settings as settings_module
from supportagent.common.clock import FrozenClock
from supportagent.common.settings import Settings, get_settings
from tests.conftest import DB_DEFAULTS, TEST_DATABASES, TEST_ENV

AS_OF = dt.date(2026, 11, 9)
SEED = 42
TOKEN = TEST_ENV["GATEWAY_SERVICE_TOKEN"]


@pytest.fixture
def bank_env(monkeypatch: pytest.MonkeyPatch, db_settings: Settings) -> Iterator[Settings]:
    monkeypatch.setattr(settings_module, "ENV_FILE", None)
    for key, value in DB_DEFAULTS.items():
        if not os.environ.get(key):
            monkeypatch.setenv(key, value)
    for key, value in TEST_DATABASES.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    db.reset_engines()
    yield get_settings()
    db.reset_engines()
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def seeded_bank(db_settings: Settings) -> Settings:
    engine = db.make_engine(db_settings.postgres.dsn("bank"))
    try:
        with Session(engine) as session:
            write(session, generate(SEED, AS_OF), reset_first=True)
            session.commit()
    finally:
        engine.dispose()
    return db_settings


@pytest.fixture
def api(
    seeded_bank: Settings, bank_db: Session, settings: Settings, frozen_clock: FrozenClock
) -> Iterator[TestClient]:
    app = create_app()

    def session_override() -> Iterator[Session]:
        yield bank_db
        bank_db.flush()

    app.dependency_overrides[get_session] = session_override
    with TestClient(app, headers={"X-Service-Token": TOKEN}) as client:
        yield client
