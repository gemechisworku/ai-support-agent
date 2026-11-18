"""Load `data/config/campaigns.yaml` (SCHED-R1): the campaigns, their selectors and their retry limits.

    from supportagent.scheduler.config import load_campaigns

    config = load_campaigns()          # path from settings CAMPAIGNS_PATH
    config.version_tag                 # "campaigns@1.0", logged with every run

An invalid file raises `ValueError` naming the file and every offending field, so a bad config stops the
service at start-up (constitution III). The ranges here also appear in `contact_rules.yaml`; a test keeps the
two files in agreement.
"""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from supportagent.compliance.config import read_yaml, validate
from supportagent.contracts.scheduler import CampaignName


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Selector(_Config):
    """Which candidates a campaign takes: `due_in_days` (an instalment due 0..3 days from today) or `dpd`
    (1..30 days past due). Callbacks take neither: they come from due callback tasks and broken promises."""

    due_in_days: tuple[int, int] | None = None
    dpd: tuple[int, int] | None = None

    @field_validator("due_in_days", "dpd")
    @classmethod
    def _range(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is not None:
            low, high = value
            if low < 0 or high < low:
                raise ValueError(f"must be a [low, high] range of non-negative days, got {list(value)}")
        return value


class RetryRule(_Config):
    delay_min: int | None = Field(None, ge=1)
    max: int = Field(ge=0)

    @model_validator(mode="after")
    def _delay_when_retrying(self) -> Self:
        if self.max > 0 and self.delay_min is None:
            raise ValueError("delay_min is required when max > 0")
        return self


class Retry(_Config):
    no_answer: RetryRule
    busy: RetryRule
    voicemail: RetryRule
    wrong_number: RetryRule


class CampaignConfig(_Config):
    enabled: bool
    run_every_min: int = Field(ge=1)
    selector: Selector
    retry: Retry


SELECTOR_KEY: dict[CampaignName, str | None] = {
    "pre_due": "due_in_days",
    "missed_payment": "dpd",
    "callback": None,
}


class CampaignsConfig(_Config):
    version: str = Field(pattern=r"^\d+\.\d+$")
    campaigns: dict[CampaignName, CampaignConfig]

    @property
    def version_tag(self) -> str:
        return f"campaigns@{self.version}"

    @model_validator(mode="after")
    def _all_three_with_their_selector(self) -> Self:
        missing = set(SELECTOR_KEY) - set(self.campaigns)
        if missing:
            raise ValueError(f"campaigns missing: {', '.join(sorted(missing))}")
        for name, key in SELECTOR_KEY.items():
            selector = self.campaigns[name].selector
            given = {k for k in ("due_in_days", "dpd") if getattr(selector, k) is not None}
            if given != ({key} if key else set()):
                wanted = f"only `{key}`" if key else "no selector"
                raise ValueError(f"{name}: selector must have {wanted}, got {sorted(given) or 'none'}")
        return self


def load_campaigns(path: Path | None = None) -> CampaignsConfig:
    path = path or default_campaigns_path()
    return validate(CampaignsConfig, read_yaml(path), path)


def default_campaigns_path() -> Path:
    from supportagent.common.settings import get_settings

    return get_settings().data.campaigns_path
