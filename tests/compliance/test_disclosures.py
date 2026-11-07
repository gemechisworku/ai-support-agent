"""T-COMP-05 · COMP-R5, COMP-R7: the versioned disclosure registry."""

from pathlib import Path

import pytest
import yaml

from supportagent.common.settings import Settings
from supportagent.compliance.disclosures import DisclosureRegistry, load_disclosures
from supportagent.contracts.compliance import RenderedDisclosure
from supportagent.contracts.outcome import Campaign, Direction

ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = ROOT / "data" / "config" / "disclosures.yaml"


@pytest.fixture
def registry() -> DisclosureRegistry:
    return load_disclosures(YAML_PATH)


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "disclosures.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _base() -> dict:  # type: ignore[type-arg]
    data = yaml.safe_load(YAML_PATH.read_text())
    assert isinstance(data, dict)
    return data


def test_load(registry: DisclosureRegistry) -> None:
    assert registry.version == "1.0"
    assert registry.version_tag == "disclosures@1.0"
    assert registry.keys == ["ai_recording", "collections", "third_party_neutral"]
    assert registry.get("collections").text.startswith("This is an attempt to collect a debt")
    assert registry.tag("collections") == "collections@1.0"
    with pytest.raises(KeyError, match="unknown disclosure"):
        registry.get("nope")


def test_default_path_is_next_to_contact_rules(settings: Settings) -> None:
    assert load_disclosures().version_tag == "disclosures@1.0"


def test_render_fills_placeholders(registry: DisclosureRegistry) -> None:
    rendered = registry.render("ai_recording", bank_name="Meridian Bank", agent_name="Ava")
    assert rendered == RenderedDisclosure(
        key="ai_recording",
        version="1.0",
        text="Hello, this is Ava, the virtual assistant from Meridian Bank. This call is recorded.",
    )
    assert rendered.tag == "ai_recording@1.0"
    third = registry.render(
        "third_party_neutral", bank_name="Meridian Bank", agent_name="Ava", first_name="Frank"
    )
    assert third.text == (
        "This is Meridian Bank calling for Frank. "
        "Please ask them to call us back at their convenience. Thank you."
    )
    assert registry.render("collections").text.endswith("used for that purpose.")  # no placeholders


def test_render_fails_on_unfilled_placeholders(registry: DisclosureRegistry) -> None:
    with pytest.raises(ValueError, match="first_name"):
        registry.render("third_party_neutral", bank_name="Meridian Bank", agent_name="Ava")
    with pytest.raises(KeyError):
        registry.render("unknown")


@pytest.mark.parametrize(
    ("direction", "campaign", "expected"),
    [
        ("outbound", "missed_payment", ["ai_recording", "collections"]),
        (Direction.outbound, Campaign.pre_due, ["ai_recording"]),
        ("outbound", "callback", ["ai_recording"]),
        ("inbound", None, ["ai_recording"]),
        ("inbound", "inbound", ["ai_recording"]),
    ],
)
def test_required_for(
    registry: DisclosureRegistry, direction: str, campaign: str | None, expected: list[str]
) -> None:
    assert registry.required_for(direction, campaign) == expected


def test_required_for_rejects_unknown_values(registry: DisclosureRegistry) -> None:
    with pytest.raises(ValueError):
        registry.required_for("sideways", None)
    with pytest.raises(ValueError):
        registry.required_for("outbound", "spam")


def test_after_verification_flag(registry: DisclosureRegistry) -> None:
    assert registry.after_verification("collections") is True
    assert registry.after_verification("ai_recording") is False


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d["disclosures"].pop("collections"), "collections"),
        (lambda d: d["disclosures"]["collections"]["required"].update(campaigns=["spam"]), "campaigns"),
        (lambda d: d["disclosures"]["ai_recording"].update(text=""), "text"),
        (lambda d: d["disclosures"]["ai_recording"].update(text="Hi {agent_name"), "placeholder"),
        (lambda d: d["disclosures"]["ai_recording"].update(tone="warm"), "tone"),
        (lambda d: d.update(version="v1"), "version"),
    ],
)
def test_invalid_files_name_the_problem(tmp_path: Path, mutate, match: str) -> None:  # type: ignore[no-untyped-def]
    data = _base()
    mutate(data)
    with pytest.raises(ValueError, match=match):
        load_disclosures(_write(tmp_path, data))
