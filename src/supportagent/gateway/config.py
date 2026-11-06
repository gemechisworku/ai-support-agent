"""The gateway's view of the settings (secrets, limits, promise rules) — read once, passed explicitly."""

import datetime as dt
from dataclasses import dataclass

from supportagent.common.settings import Settings, get_settings

SESSION_IDLE_TTL = dt.timedelta(minutes=30)
SESSION_MAX_TTL = dt.timedelta(minutes=60)
MAX_ATTEMPTS = 2  # verify_identity failures before the session locks (F4)


@dataclass(frozen=True)
class RateLimits:
    session_per_minute: int = 20
    session_per_call: int = 150
    search_per_minute: int = 10
    credential_per_minute: int = 120


@dataclass(frozen=True)
class GatewayConfig:
    worker_token: str
    service_token: str
    audit_salt: str
    promise_max_days: int = 14
    allow_partial: bool = True
    human_agent_available: bool = True
    transfer_extension: str = "200"
    limits: RateLimits = RateLimits()

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "GatewayConfig":
        s = settings or get_settings()
        return cls(
            worker_token=s.services.gateway_worker_token.get_secret_value(),
            service_token=s.services.gateway_service_token.get_secret_value(),
            audit_salt=s.services.audit_hash_salt.get_secret_value(),
            promise_max_days=s.data.promise_max_days,
            human_agent_available=s.services.human_agent_available,
            transfer_extension=s.livekit.human_agent_extension,
        )
