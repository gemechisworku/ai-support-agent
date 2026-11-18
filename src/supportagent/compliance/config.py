"""Load the versioned compliance configuration from `data/config/` (COMP-R1).

    from supportagent.compliance.config import load_rules

    rules = load_rules()            # path from settings CONTACT_RULES_PATH
    rules.version_tag               # "contact_rules@1.0"

Invalid files raise `ValueError` naming the file and every offending field, so a bad config stops a
service at start-up rather than silently relaxing a rule (constitution III).
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from supportagent.contracts.compliance import ContactRules

# Repo root (src/supportagent/compliance/config.py → parents[3]); relative config paths fall back to it,
# so tools and tests work from any working directory.
REPO_ROOT = Path(__file__).resolve().parents[3]


def resolve(path: Path) -> Path:
    """`path` as given if it exists (or is absolute), else relative to the repo root if that exists."""
    if path.is_absolute() or path.exists():
        return path
    candidate = REPO_ROOT / path
    return candidate if candidate.exists() else path


def read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML mapping, turning every failure into a `ValueError` that names the file."""
    path = resolve(path)
    if not path.exists():
        raise ValueError(f"{path}: config file not found")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data


def validate[T: BaseModel](model: type[T], data: dict[str, Any], path: Path) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}" for err in exc.errors()
        )
        raise ValueError(f"{resolve(path)}: invalid {model.__name__}: {problems}") from exc


def default_rules_path() -> Path:
    from supportagent.common.settings import get_settings

    return get_settings().data.contact_rules_path


def load_rules(path: Path | None = None) -> ContactRules:
    """Load and validate `contact_rules.yaml` (default: settings `CONTACT_RULES_PATH`)."""
    path = path if path is not None else default_rules_path()
    return validate(ContactRules, read_yaml(path), path)
