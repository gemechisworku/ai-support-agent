"""Scheduler test fixtures: persona-shaped candidates, a fake service client and a frozen clock.

All data is synthetic. The personas mirror `bank/seed/personas.py` (Frank 5 days past due, Hana 22, Dawit 12
with an open dispute, Sam 15 with `cease_contact`, Olivia due soon); the ids here are test-local.
"""

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from supportagent.common import clock
from supportagent.compliance.config import load_rules
from supportagent.contracts.compliance import ContactRules
from supportagent.contracts.gateway import Candidate, ContactPrefs
from supportagent.contracts.scheduler import CampaignName
from supportagent.scheduler.config import CampaignsConfig, load_campaigns

ROOT = Path(__file__).resolve().parents[2]
# 10:00 in New York (UTC-5 in November): inside the 08:00-21:00 window.
NOW = dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.UTC)
TODAY = NOW.date()


def prefs(**over: object) -> ContactPrefs:
    base: dict[str, object] = {
        "consent_voice_ai": True,
        "dnc": False,
        "cease_contact": False,
        "dispute_open": False,
        "preferred_window": None,
    }
    return ContactPrefs(**{**base, **over})  # type: ignore[arg-type]


def candidate(
    customer_id: int,
    loan_id: int,
    campaign: CampaignName = "missed_payment",
    *,
    dpd: int = 0,
    due_in_days: int | None = None,
    timezone: str | None = "America/New_York",
    contact: ContactPrefs | None = None,
    attempts: int = 0,
    last_conversation_at: dt.datetime | None = None,
    task_id: int | None = None,
) -> Candidate:
    due = (
        TODAY + dt.timedelta(days=due_in_days) if due_in_days is not None else TODAY - dt.timedelta(days=dpd)
    )
    built = Candidate(  # validated, so `campaign` is the production `Campaign` enum
        customer_id=customer_id,
        loan_id=loan_id,
        phone=f"+1555010{customer_id:04d}",
        timezone="America/New_York",
        campaign=campaign,  # type: ignore[arg-type]
        due_date=due,
        amount_due=Decimal("200.72"),
        days_past_due=dpd,
        contact=contact or prefs(),
        attempts_last_7d=attempts,
        last_conversation_at=last_conversation_at,
        task_id=task_id,
    )
    if timezone != built.timezone:  # `None` models a missing zone, which the contract types as str
        built = built.model_copy(update={"timezone": timezone})
    return built


def frank() -> Candidate:
    return candidate(1, 11, dpd=5)


def hana() -> Candidate:
    return candidate(2, 21, dpd=22)


def dawit() -> Candidate:
    return candidate(3, 31, dpd=12, contact=prefs(dispute_open=True))


def sam() -> Candidate:
    return candidate(4, 41, dpd=15, contact=prefs(cease_contact=True))


def olivia() -> Candidate:
    return candidate(5, 51, "pre_due", due_in_days=2)


class FakeGateway:
    """The scheduler's `CandidateSource`: the service-scope candidates, held in memory."""

    def __init__(self, **by_campaign: list[Candidate]) -> None:
        self.by_campaign = by_campaign
        self.calls: list[str] = []

    async def candidates(self, campaign: CampaignName) -> list[Candidate]:
        self.calls.append(campaign)
        return list(self.by_campaign.get(campaign, []))


@pytest.fixture
def rules() -> ContactRules:
    return load_rules(ROOT / "data" / "config" / "contact_rules.yaml")


@pytest.fixture
def config() -> CampaignsConfig:
    return load_campaigns(ROOT / "data" / "config" / "campaigns.yaml")


@pytest.fixture
def frozen() -> Iterator[None]:
    with clock.freeze_clock(NOW):
        yield
