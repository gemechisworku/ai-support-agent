"""Shared pytest fixtures. T-PLAT-05 extends this with database fixtures and a frozen clock."""

from collections.abc import Iterator

import pytest

from supportagent.common import settings as settings_module
from supportagent.common.settings import Settings, get_settings

# Values for required secrets so tests never depend on a developer's local .env.
TEST_ENV = {
    "POSTGRES_PASSWORD": "test-only",
    "GATEWAY_SERVICE_TOKEN": "test-service-token",
    "GATEWAY_WORKER_TOKEN": "test-worker-token",
    "AUDIT_HASH_SALT": "test-salt",
    "SCHEDULER_TOKEN": "test-scheduler-token",
}


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Settings built from the environment only (the local .env is ignored), cache cleared around the test."""
    monkeypatch.setattr(settings_module, "ENV_FILE", None)
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()
