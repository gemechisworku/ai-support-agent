"""T-COMP-01 · COMP-R1, COMP-R7: versioned contact rules loaded into a validated model."""

from datetime import time
from pathlib import Path

import pytest
import yaml

from supportagent.common.settings import Settings
from supportagent.compliance import config
from supportagent.compliance.config import load_rules
from supportagent.contracts.compliance import ContactRules

ROOT = Path(__file__).resolve().parents[2]
RULES_YAML = ROOT / "data" / "config" / "contact_rules.yaml"


def _write(tmp_path: Path, data: dict) -> Path:  # type: ignore[type-arg]
    path = tmp_path / "contact_rules.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _base() -> dict:  # type: ignore[type-arg]
    data = yaml.safe_load(RULES_YAML.read_text())
    assert isinstance(data, dict)
    return data


def test_load_checked_in_rules() -> None:
    rules = load_rules(RULES_YAML)
    assert isinstance(rules, ContactRules)
    assert rules.version == "1.0"
    assert rules.version_tag == "contact_rules@1.0"
    assert rules.calling_window.start == time(8, 0)
    assert rules.calling_window.end == time(21, 0)
    assert rules.frequency.max_attempts == 7
    assert rules.frequency.window_days == 7
    assert rules.frequency.cool_off_days_after_conversation == 7
    assert (rules.retries.no_answer_retry_minutes, rules.retries.busy_retry_minutes) == (120, 30)
    assert (rules.retries.voicemail_retry_minutes, rules.retries.max_attempts_per_day) == (240, 2)
    assert rules.campaigns.pre_due_reminder.due_within_days == 3
    assert (rules.campaigns.missed_payment.dpd_min, rules.campaigns.missed_payment.dpd_max) == (1, 30)


def test_default_path_comes_from_settings(settings: Settings) -> None:
    assert settings.data.contact_rules_path == Path("data/config/contact_rules.yaml")
    assert load_rules().version_tag == "contact_rules@1.0"


def test_path_overridable_via_settings(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = _base()
    data["version"] = "9.9"
    monkeypatch.setenv("CONTACT_RULES_PATH", str(_write(tmp_path, data)))
    from supportagent.common.settings import get_settings

    get_settings.cache_clear()
    assert load_rules().version_tag == "contact_rules@9.9"


def test_relative_path_resolves_from_repo_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # not the repo root
    assert load_rules(Path("data/config/contact_rules.yaml")).version == "1.0"
    assert config.resolve(Path("does/not/exist.yaml")) == Path("does/not/exist.yaml")


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda d: d["calling_window"].update(end="08:00"), "calling_window"),
        (lambda d: d["calling_window"].update(start="22:00"), "calling_window"),
        (lambda d: d["calling_window"].update(start=480), "calling_window.start"),  # YAML 08:00 unquoted
        (lambda d: d["calling_window"].update(end="9pm"), "calling_window.end"),
        (lambda d: d["frequency"].update(max_attempts=-1), "frequency.max_attempts"),
        (lambda d: d["frequency"].update(window_days=0), "frequency.window_days"),
        (lambda d: d["frequency"].update(cool_off_days_after_conversation=-2), "frequency.cool_off"),
        (lambda d: d["retries"].update(busy_retry_minutes=0), "retries.busy_retry_minutes"),
        (lambda d: d["campaigns"]["missed_payment"].update(dpd_min=31), "campaigns.missed_payment"),
        (lambda d: d.update(surprise=True), "surprise"),
        (lambda d: d["frequency"].update(max_attempts_per_week=7), "frequency.max_attempts_per_week"),
        (lambda d: d.update(version="one"), "version"),
        (lambda d: d.pop("jurisdiction"), "jurisdiction"),
    ],
)
def test_invalid_rules_name_the_field(tmp_path: Path, mutate, field: str) -> None:  # type: ignore[no-untyped-def]
    data = _base()
    mutate(data)
    with pytest.raises(ValueError, match=field.replace(".", r"\.")) as err:
        load_rules(_write(tmp_path, data))
    assert "contact_rules.yaml" in str(err.value)


def test_not_a_mapping_or_bad_yaml(tmp_path: Path) -> None:
    path = tmp_path / "contact_rules.yaml"
    path.write_text("- just\n- a list\n")
    with pytest.raises(ValueError, match="mapping"):
        load_rules(path)
    path.write_text("version: [unclosed\n")
    with pytest.raises(ValueError, match="YAML"):
        load_rules(path)


def test_missing_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        load_rules(tmp_path / "nope.yaml")


def test_rules_are_immutable() -> None:
    rules = load_rules(RULES_YAML)
    with pytest.raises(ValueError):
        rules.frequency.max_attempts = 99  # type: ignore[misc]
