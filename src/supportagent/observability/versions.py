"""Version stamps for every call (OBS-R3, constitution VII, F11).

`compute_versions()` returns `contracts.outcome.Versions{prompt, policy, model}`. The agent calls it
once per call at session start (T-AGENT-02) and records it in the call outcome::

    from supportagent.observability.versions import compute_versions

    compute_versions(get_settings())
    # Versions(prompt='1.2.0+3f9a0c12',
    #          policy='kb:8e21d0aa;rules:1;disc:1',
    #          model='llm:llamacpp/llama-3.1-8b-instruct;stt:small.en;tts:af_heart;'
    #                'embed:nomic-embed-text-v1.5')

Formula (specs/10-observability/plan.md); `sha8` = first 8 hex chars of SHA-256:
- `prompt` = `{registry_version}+{sha8(prompt files)}`. `registry_version` is the stripped text of
  `prompts/VERSION`. Prompt files are every `*.md` / `*.yaml` / `*.yml` under the prompts directory
  (recursively, `__pycache__` and hidden paths skipped), hashed in sorted relative-path order as
  `relpath \\0 bytes \\0` per file.
- `policy` = `kb:{sha8(sorted "doc_id@version" lines)};rules:{rules version};disc:{disclosures version}`.
  The KB part follows each policy document's front-matter `doc_id@version`, so a policy change must
  bump its version to change the stamp. Rules and disclosures versions are the top-level `version:`
  key of `contact_rules.yaml` / `disclosures.yaml` in the config directory.
- `model` = `llm:{provider}/{model};stt:{whisper_model};tts:{tts_voice};embed:{embed_model}`.

Pure: it reads only the given directories and settings, never mtimes, and never raises on missing or
malformed inputs. A missing input becomes the component `none`, so early milestones work before the
prompt registry or the config files exist.
"""

import hashlib
from collections.abc import Iterable
from pathlib import Path

import yaml

from supportagent.common.settings import ModelsSettings, Settings
from supportagent.contracts.outcome import Versions

NONE = "none"
PROMPT_SUFFIXES = (".md", ".yaml", ".yml")

# Repo root: src/supportagent/observability/versions.py → parents[3].
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROMPTS_DIR = _ROOT / "src" / "supportagent" / "agent" / "prompts"
DEFAULT_POLICIES_DIR = _ROOT / "data" / "policies"
DEFAULT_CONFIG_DIR = _ROOT / "data" / "config"


def _sha8(chunks: Iterable[bytes]) -> str:
    h = hashlib.sha256()
    for chunk in chunks:
        h.update(chunk)
    return h.hexdigest()[:8]


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _prompt_files(prompts_dir: Path) -> list[Path]:
    if not prompts_dir.is_dir():
        return []
    files = []
    for path in prompts_dir.rglob("*"):
        rel = path.relative_to(prompts_dir)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        if path.is_file() and path.suffix in PROMPT_SUFFIXES:
            files.append(path)
    return sorted(files, key=lambda p: p.relative_to(prompts_dir).as_posix())


def prompt_version(prompts_dir: Path) -> str:
    """`{registry_version}+{sha8}` for the prompt registry; `none` parts when missing."""
    raw = _read_text(prompts_dir / "VERSION")
    registry = raw.strip() if raw and raw.strip() else NONE
    files = _prompt_files(prompts_dir)
    if not files:
        return f"{registry}+{NONE}"

    def chunks() -> Iterable[bytes]:
        for path in files:
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            yield path.relative_to(prompts_dir).as_posix().encode() + b"\0" + data + b"\0"

    return f"{registry}+{_sha8(chunks())}"


def _front_matter(text: str) -> dict[str, object]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _policy_lines(policies_dir: Path) -> list[str]:
    if not policies_dir.is_dir():
        return []
    lines = []
    for path in sorted(policies_dir.glob("*.md")):
        meta = _front_matter(_read_text(path) or "")
        doc_id = meta.get("doc_id") or path.stem
        version = meta.get("version") or "unversioned"
        lines.append(f"{doc_id}@{version}")
    return sorted(lines)


def _config_version(path: Path) -> str:
    text = _read_text(path)
    if text is None:
        return NONE
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return NONE
    if not isinstance(data, dict) or data.get("version") in (None, ""):
        return NONE
    return str(data["version"])


def policy_version(policies_dir: Path, config_dir: Path) -> str:
    """`kb:{sha8};rules:{v};disc:{v}` for the KB corpus and the versioned contact rules/disclosures."""
    lines = _policy_lines(policies_dir)
    kb = _sha8(["\n".join(lines).encode()]) if lines else NONE
    rules = _config_version(config_dir / "contact_rules.yaml")
    disc = _config_version(config_dir / "disclosures.yaml")
    return f"kb:{kb};rules:{rules};disc:{disc}"


def model_version(models: ModelsSettings) -> str:
    """`llm:{provider}/{model};stt:…;tts:…;embed:…` — the llm part is VOICE's `provider/model` form."""
    return (
        f"llm:{models.llm_provider}/{models.llm_model};stt:{models.whisper_model};"
        f"tts:{models.tts_voice};embed:{models.embed_model}"
    )


def compute_versions(
    settings: Settings,
    *,
    prompts_dir: Path = DEFAULT_PROMPTS_DIR,
    policies_dir: Path = DEFAULT_POLICIES_DIR,
    config_dir: Path = DEFAULT_CONFIG_DIR,
) -> Versions:
    """The version bundle for a call. Deterministic; never raises on missing or malformed inputs."""
    return Versions(
        prompt=prompt_version(prompts_dir),
        policy=policy_version(policies_dir, config_dir),
        model=model_version(settings.models),
    )
