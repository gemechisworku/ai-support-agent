"""Bank test fixtures.

- `bank_env`: process settings pointed at `bank_test` (for code that calls `get_settings()` / Alembic),
  restored after the test.
"""

import os
from collections.abc import Iterator

import pytest

from supportagent.common import db
from supportagent.common import settings as settings_module
from supportagent.common.settings import Settings, get_settings
from tests.conftest import DB_DEFAULTS, TEST_DATABASES


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
