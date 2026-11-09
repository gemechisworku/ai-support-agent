"""Versioned disclosure scripts and the after-the-fact disclosure check (COMP-R5, COMP-R6, COMP-R7).

    registry = load_disclosures()                       # data/config/disclosures.yaml
    registry.required_for("outbound", "missed_payment")  # ["ai_recording", "collections"]
    said = registry.render("ai_recording", bank_name="Meridian Bank", agent_name="Ava")
    said.text, said.tag                                  # words to speak, "ai_recording@1.0"

    check_disclosures(outcome, events)                   # [] when the call was compliant

The agent (T-AGENT-04) speaks the rendered texts and records their tags; the evals (T-EVAL-03) and the
console use `check_disclosures` to prove the rules held. The check is deterministic code, not a prompt.
"""

import string
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from supportagent.compliance.config import read_yaml, validate
from supportagent.contracts.compliance import CallEvent, Disclosure, RenderedDisclosure, Violation
from supportagent.contracts.outcome import CallOutcome, Campaign, ContactResult, Direction

REQUIRED_KEYS = ("ai_recording", "collections", "third_party_neutral")

# Tools whose results are account data (constitution II/III): never before verification, and on a
# missed-payment call never before the collections disclosure.
ACCOUNT_DATA_TOOLS = frozenset({"get_loan_status", "get_recent_transactions", "get_interaction_history"})

# Disclosures are owed only when someone actually answered.
_CONVERSATION = {ContactResult.right_party, ContactResult.third_party}


def _placeholders(text: str) -> set[str]:
    try:
        return {name for _, name, _, _ in string.Formatter().parse(text) if name}
    except ValueError as exc:  # e.g. an unclosed "{"
        raise ValueError(f"malformed placeholder: {exc}") from exc


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Requirement(_Config):
    all: bool = False
    campaigns: list[Campaign] = Field(default_factory=list)
    after_verification: bool = False


class _DisclosureConfig(_Config):
    text: str = Field(min_length=1)
    required: _Requirement = Field(default_factory=_Requirement)

    @field_validator("text")
    @classmethod
    def _well_formed(cls, text: str) -> str:
        _placeholders(text)
        return text


class _DisclosureFile(_Config):
    version: str = Field(pattern=r"^\d+\.\d+$")
    disclosures: dict[str, _DisclosureConfig]

    @model_validator(mode="after")
    def _has_required_keys(self) -> Self:
        missing = [k for k in REQUIRED_KEYS if k not in self.disclosures]
        if missing:
            raise ValueError(f"missing disclosures: {', '.join(missing)}")
        return self


class DisclosureRegistry:
    """The disclosure scripts of one versioned file (`disclosures@<version>`)."""

    def __init__(self, config: _DisclosureFile) -> None:
        self._config = config

    @property
    def version(self) -> str:
        return self._config.version

    @property
    def version_tag(self) -> str:
        return f"disclosures@{self.version}"

    @property
    def keys(self) -> list[str]:
        return list(self._config.disclosures)

    def _entry(self, key: str) -> _DisclosureConfig:
        try:
            return self._config.disclosures[key]
        except KeyError:
            raise KeyError(f"unknown disclosure {key!r}") from None

    def get(self, key: str) -> Disclosure:
        return Disclosure(key=key, version=self.version, text=self._entry(key).text)

    def tag(self, key: str) -> str:
        self._entry(key)
        return f"{key}@{self.version}"

    def after_verification(self, key: str) -> bool:
        return self._entry(key).required.after_verification

    def render(self, key: str, **values: str) -> RenderedDisclosure:
        """Fill `{placeholders}` from `values`; any placeholder without a value is an error."""
        text = self._entry(key).text
        unfilled = sorted(_placeholders(text) - values.keys())
        if unfilled:
            raise ValueError(f"disclosure {key!r} needs values for: {', '.join(unfilled)}")
        return RenderedDisclosure(key=key, version=self.version, text=text.format(**values))

    def required_for(self, direction: Direction | str, campaign: Campaign | str | None) -> list[str]:
        """Keys a call of this direction and campaign must deliver, in script order."""
        Direction(direction)  # validates
        camp = Campaign(campaign) if campaign is not None else None
        return [
            key
            for key, entry in self._config.disclosures.items()
            if entry.required.all or (camp is not None and camp in entry.required.campaigns)
        ]


def default_disclosures_path() -> Path:
    """`disclosures.yaml` next to the contact rules file (settings `CONTACT_RULES_PATH`)."""
    from supportagent.common.settings import get_settings

    return get_settings().data.contact_rules_path.with_name("disclosures.yaml")


def load_disclosures(path: Path | None = None) -> DisclosureRegistry:
    path = path if path is not None else default_disclosures_path()
    return DisclosureRegistry(validate(_DisclosureFile, read_yaml(path), path))


def check_disclosures(
    outcome: CallOutcome, events: list[CallEvent], registry: DisclosureRegistry | None = None
) -> list[Violation]:
    """Violations of COMP-R6 for one finished call; `[]` means compliant.

    1. Every required disclosure for the call's direction/campaign is in `outcome.disclosures_given`
       (only when someone answered; `after_verification` ones only when the customer was verified).
    2. No account-data tool ran before identity was verified.
    3. On a missed-payment call, no account-data tool ran before the collections disclosure.
    """
    registry = registry if registry is not None else load_disclosures()
    violations: list[Violation] = []

    if outcome.contact_result in _CONVERSATION:
        given = {tag.split("@", 1)[0] for tag in outcome.disclosures_given}
        for key in registry.required_for(outcome.direction, outcome.campaign):
            if registry.after_verification(key) and not outcome.verified:
                continue
            if key not in given:
                violations.append(
                    Violation(
                        code=f"missing_disclosure:{key}",
                        detail=f"{key} is required on {outcome.direction} {outcome.campaign} calls",
                    )
                )

    ordered = sorted(events, key=lambda e: e.ts)  # stable: equal timestamps keep their given order
    verified = collections_said = False
    early_data = early_collections = False
    for event in ordered:
        if event.kind == "verified":
            verified = True
        elif event.kind == "disclosure" and event.name == "collections":
            collections_said = True
        elif event.kind == "tool" and event.name in ACCOUNT_DATA_TOOLS:
            early_data = early_data or not verified
            if outcome.campaign == Campaign.missed_payment:
                early_collections = early_collections or not collections_said
    if early_data:
        violations.append(
            Violation(code="data_before_verification", detail="an account-data tool ran before verification")
        )
    if early_collections:
        violations.append(
            Violation(
                code="data_before_collections_disclosure",
                detail="an account-data tool ran before the collections disclosure",
            )
        )
    return violations
