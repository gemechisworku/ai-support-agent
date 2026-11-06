"""T-PLAT-02 · PLAT-R1: one typed Settings object, read from env / .env only."""

import re
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from supportagent.common import settings as settings_module
from supportagent.common.settings import Settings, env_var_names, get_settings, load_settings
from tests.conftest import TEST_ENV

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.example"


def _example_keys() -> set[str]:
    keys = set()
    for line in ENV_EXAMPLE.read_text().splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if m:
            keys.add(m.group(1))
    return keys


def test_get_settings_is_cached(settings: Settings) -> None:
    assert get_settings() is settings
    get_settings.cache_clear()
    assert get_settings() is not settings


def test_env_override(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("BANK_AS_OF", "2026-11-09")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    get_settings.cache_clear()
    s = get_settings()
    assert s.postgres.port == 6543
    assert s.data.bank_as_of == date(2026, 11, 9)


def test_defaults_without_env_file(settings: Settings) -> None:
    assert settings.postgres.port == 5432
    assert settings.postgres.bank_db == "bank"
    assert settings.data.bank_as_of is None
    assert settings.models.llm_provider == "ollama"
    assert settings.services.gateway_worker_token.get_secret_value() == "test-worker-token"


def test_every_env_example_key_is_a_setting() -> None:
    known = env_var_names()
    assert _example_keys() - known == set(), "keys in .env.example without a settings field"
    assert known - _example_keys() == set(), "settings fields missing from .env.example"


def test_env_example_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented example file is itself a valid configuration (inline comments, empty values)."""
    for key in _example_keys():
        monkeypatch.delenv(key, raising=False)
    s = load_settings(ENV_EXAMPLE)
    assert s.postgres.host == "localhost"
    assert s.data.kb_min_similarity == 0.55
    assert s.data.bank_as_of is None  # empty value
    assert s.models.anthropic_api_key is None
    assert s.services.human_agent_available is True
    assert s.persona.bank_name == "Meridian Bank"


def test_dsn_builds_psycopg_url(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "supportagent",
    }.items():
        monkeypatch.setenv(key, value)  # independent of the runner's env (e.g. compose sets host=postgres)
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/word")
    monkeypatch.setenv("KB_DB", "kb_test")
    s = load_settings(None)
    assert s.postgres.dsn("bank") == "postgresql+psycopg://supportagent:p%40ss%2Fword@localhost:5432/bank"
    assert s.postgres.dsn("kb").endswith("/kb_test")


@pytest.mark.parametrize("secret", sorted(TEST_ENV))
def test_missing_secret_fails_clearly(secret: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(secret)
    with pytest.raises(ValidationError, match=f"(?i){secret}"):  # error names the env variable
        load_settings(None)


def test_hosted_llm_requires_api_key(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        load_settings(None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert load_settings(None).models.llm_provider == "anthropic"


def test_secrets_are_not_printed(settings: Settings) -> None:
    assert "test-only" not in repr(settings)
    assert "test-worker-token" not in str(settings.model_dump())


def test_no_os_environ_outside_settings() -> None:
    offenders = [
        p
        for p in (ROOT / "src").rglob("*.py")
        if "os.environ" in p.read_text() or "os.getenv" in p.read_text()
    ]
    assert offenders == []


def test_env_file_default_is_dotenv() -> None:
    assert Path(".env") == settings_module.ENV_FILE
