"""Typed configuration for every module (PLAT-R1).

All configuration comes from environment variables and an optional `.env` file, through
`get_settings()`. No other module reads the environment directly. Environment variable names are
exactly the ones in `.env.example`; a unit test keeps the two in sync.

Usage::

    from supportagent.common.settings import get_settings

    s = get_settings()
    s.postgres.dsn("bank")
    s.services.gateway_worker_token.get_secret_value()

Required secrets have no default, so a missing one fails at startup with a `ValidationError` naming
the variable. The values in `.env.example` are local-only placeholders.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import quote_plus

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The env file read by get_settings(), relative to the working directory. Tests set it to None.
ENV_FILE: Path | None = Path(".env")

_CONFIG = SettingsConfigDict(
    env_file_encoding="utf-8",
    env_ignore_empty=True,  # `BANK_AS_OF=` in .env means "unset", not ""
    extra="ignore",  # one .env feeds every group
)


class _Group(BaseSettings):
    model_config = _CONFIG


class PostgresSettings(_Group):
    """Postgres 16 + pgvector from docker compose; one server, databases `bank` and `kb`."""

    host: str = Field("localhost", validation_alias="POSTGRES_HOST")
    port: int = Field(5432, validation_alias="POSTGRES_PORT")
    user: str = Field("supportagent", validation_alias="POSTGRES_USER")
    password: SecretStr = Field(validation_alias="POSTGRES_PASSWORD")
    bank_db: str = Field("bank", validation_alias="BANK_DB")
    kb_db: str = Field("kb", validation_alias="KB_DB")

    def dsn(self, database: Literal["bank", "kb"]) -> str:
        """SQLAlchemy URL for the psycopg 3 driver, e.g. `postgresql+psycopg://u:p@host:5432/bank`."""
        name = self.bank_db if database == "bank" else self.kb_db
        pw = quote_plus(self.password.get_secret_value())
        return f"postgresql+psycopg://{quote_plus(self.user)}:{pw}@{self.host}:{self.port}/{name}"


class DataSettings(_Group):
    """Mock bank, knowledge base and policy configuration."""

    bank_as_of: date | None = None
    kb_embedder: Literal["llamacpp", "fake"] = "llamacpp"
    kb_min_similarity: float = Field(0.55, ge=0.0, le=1.0)
    contact_rules_path: Path = Path("data/config/contact_rules.yaml")
    promise_max_days: int = Field(14, ge=1)
    transcripts_dir: Path = Path("var/transcripts")


class ServicesSettings(_Group):
    """Internal service URLs and the tokens between them."""

    bank_api_url: str = "http://localhost:8001"
    gateway_url: str = "http://localhost:8002/mcp"
    gateway_service_token: SecretStr
    gateway_worker_token: SecretStr
    audit_hash_salt: SecretStr
    human_agent_available: bool = True
    scheduler_url: str = "http://localhost:8003"
    scheduler_token: SecretStr
    sched_max_concurrent: int = Field(1, ge=1)
    max_call_s: int = Field(600, ge=1)


class ModelsSettings(_Group):
    """Native model servers (Metal) and the hosted-LLM switch (F14)."""

    # llama.cpp `llama-server` (OpenAI-compatible, base URLs include /v1). The model names are the
    # servers' `--alias`; the GGUF paths are what scripts/models/ starts them with.
    llm_url: str = "http://localhost:8090/v1"
    embed_url: str = "http://localhost:8082/v1"
    llm_provider: Literal["llamacpp", "anthropic"] = "llamacpp"
    llm_model: str = "llama-3.1-8b-instruct"
    embed_model: str = "nomic-embed-text-v1.5"
    llm_gguf: Path = Path("models/llm/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf")
    embed_gguf: Path = Path("models/embed/nomic-embed-text-v1.5.Q8_0.gguf")
    stt_url: str = "http://localhost:8178"
    whisper_model: str = "small.en"
    silence_reprompt_s: float = Field(6, gt=0)
    silence_end_s: float = Field(15, gt=0)
    tts_url: str = "http://localhost:8880"
    tts_voice: str = "af_heart"
    anthropic_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _hosted_llm_needs_key(self) -> Self:
        if self.llm_provider == "anthropic" and self.anthropic_api_key is None:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        return self


class LiveKitSettings(_Group):
    """LiveKit server, LiveKit SIP and the local Asterisk PBX.

    The key/secret defaults are the ones `livekit-server --dev` uses.
    """

    livekit_url: str = "ws://localhost:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: SecretStr = SecretStr("secret")
    sip_outbound_trunk_id: str | None = None
    sip_inbound_trunk_id: str | None = None
    support_extension: str = "100"
    human_agent_extension: str = "200"
    tel_advertised_ip: str | None = None
    asterisk_host: str = "localhost"
    transfer_timeout_s: float = Field(25, gt=0)


class LangfuseSettings(_Group):
    """Optional self-hosted Langfuse; tracing is off unless both keys are set."""

    host: str = Field("http://localhost:3000", validation_alias="LANGFUSE_HOST")
    public_key: str | None = Field(None, validation_alias="LANGFUSE_PUBLIC_KEY")
    secret_key: SecretStr | None = Field(None, validation_alias="LANGFUSE_SECRET_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.public_key and self.secret_key)


class PersonaSettings(_Group):
    """How the agent introduces itself (PRD §12: bank and agent name are configuration)."""

    bank_name: str = "Meridian Bank"
    agent_name: str = "Ava"


_GROUPS: dict[str, type[_Group]] = {
    "postgres": PostgresSettings,
    "data": DataSettings,
    "services": ServicesSettings,
    "models": ModelsSettings,
    "livekit": LiveKitSettings,
    "langfuse": LangfuseSettings,
    "persona": PersonaSettings,
}


class Settings(BaseModel):
    """All configuration, grouped. Each module reads only its own group."""

    postgres: PostgresSettings
    data: DataSettings
    services: ServicesSettings
    models: ModelsSettings
    livekit: LiveKitSettings
    langfuse: LangfuseSettings
    persona: PersonaSettings


def load_settings(env_file: Path | None = ENV_FILE) -> Settings:
    """Build settings from the environment plus `env_file` (environment wins). Uncached."""
    groups = {name: cls(_env_file=env_file) for name, cls in _GROUPS.items()}
    return Settings(**groups)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, read once. Tests call `get_settings.cache_clear()`."""
    return load_settings(ENV_FILE)


def env_var_names() -> set[str]:
    """Every environment variable the settings read (kept equal to the keys of `.env.example`)."""
    names: set[str] = set()
    for cls in _GROUPS.values():
        for field_name, field in cls.model_fields.items():
            alias = field.validation_alias
            names.add(alias if isinstance(alias, str) else field_name.upper())
    return names
