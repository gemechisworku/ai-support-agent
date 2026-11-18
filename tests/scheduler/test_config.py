"""T-SCHED-01 · SCHED-R1: `campaigns.yaml` is versioned, validated and agrees with the contact rules."""

from pathlib import Path

import pytest
import yaml

from supportagent.contracts.compliance import ContactRules
from supportagent.scheduler.config import CampaignsConfig, load_campaigns

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "data" / "config" / "campaigns.yaml"


def raw() -> dict[str, object]:
    return yaml.safe_load(PATH.read_text())  # type: ignore[no-any-return]


def write(tmp_path: Path, data: object) -> Path:
    p = tmp_path / "campaigns.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_the_file_is_versioned_and_defines_three_campaigns(config: CampaignsConfig) -> None:
    assert config.version == "1.0" and config.version_tag == "campaigns@1.0"
    assert set(config.campaigns) == {"pre_due", "missed_payment", "callback"}
    assert all(c.enabled for c in config.campaigns.values())


def test_selectors_are_the_prd_ranges(config: CampaignsConfig) -> None:
    assert config.campaigns["pre_due"].selector.due_in_days == (0, 3)
    assert config.campaigns["missed_payment"].selector.dpd == (1, 30)
    assert config.campaigns["callback"].selector.due_in_days is None


def test_the_selectors_agree_with_the_contact_rules(config: CampaignsConfig, rules: ContactRules) -> None:
    """Two files state the same ranges; a drift between them would change who gets called."""
    assert config.campaigns["pre_due"].selector.due_in_days == (
        0,
        rules.campaigns.pre_due_reminder.due_within_days,
    )
    mp = rules.campaigns.missed_payment
    assert config.campaigns["missed_payment"].selector.dpd == (mp.dpd_min, mp.dpd_max)


def test_the_retry_delays_agree_with_the_contact_rules(config: CampaignsConfig, rules: ContactRules) -> None:
    for campaign in config.campaigns.values():
        retry = campaign.retry
        assert retry.no_answer.delay_min == rules.retries.no_answer_retry_minutes
        assert retry.busy.delay_min == rules.retries.busy_retry_minutes
        assert retry.voicemail.delay_min == rules.retries.voicemail_retry_minutes
        assert retry.wrong_number.max == 0


def test_a_missing_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="config file not found"):
        load_campaigns(tmp_path / "nope.yaml")


def test_a_bad_version_names_the_file_and_field(tmp_path: Path) -> None:
    data = raw()
    data["version"] = "one"
    with pytest.raises(ValueError, match=r"campaigns\.yaml.*version"):
        load_campaigns(write(tmp_path, data))


def test_an_unknown_campaign_is_refused(tmp_path: Path) -> None:
    data = raw()
    data["campaigns"]["win_back"] = data["campaigns"]["callback"]  # type: ignore[index]
    with pytest.raises(ValueError, match="win_back"):
        load_campaigns(write(tmp_path, data))


def test_a_missing_campaign_is_refused(tmp_path: Path) -> None:
    data = raw()
    del data["campaigns"]["callback"]  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="callback"):
        load_campaigns(write(tmp_path, data))


@pytest.mark.parametrize(
    ("campaign", "selector"),
    [
        ("pre_due", {"dpd": [1, 30]}),  # the wrong selector for the campaign
        ("missed_payment", {"due_in_days": [0, 3]}),
        ("missed_payment", {"dpd": [30, 1]}),  # reversed range
        ("pre_due", {"due_in_days": [-1, 3]}),  # negative
        ("callback", {"dpd": [1, 30]}),  # callbacks take no selector
    ],
)
def test_a_bad_selector_is_refused(tmp_path: Path, campaign: str, selector: dict[str, object]) -> None:
    data = raw()
    data["campaigns"][campaign]["selector"] = selector  # type: ignore[index]
    with pytest.raises(ValueError, match="selector"):
        load_campaigns(write(tmp_path, data))


def test_an_unknown_field_is_refused(tmp_path: Path) -> None:
    data = raw()
    data["campaigns"]["callback"]["run_every_minutes"] = 5  # type: ignore[index]
    with pytest.raises(ValueError, match="run_every_minutes"):
        load_campaigns(write(tmp_path, data))


def test_the_path_comes_from_settings() -> None:
    from supportagent.common.settings import DataSettings

    assert DataSettings(_env_file=None).campaigns_path == Path("data/config/campaigns.yaml")
