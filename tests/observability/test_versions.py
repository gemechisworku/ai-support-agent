"""T-OBS-02 · OBS-R3: deterministic version stamps for prompts, policies and models."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from supportagent.common.settings import Settings
from supportagent.contracts.outcome import Versions
from supportagent.observability import versions
from supportagent.observability.versions import NONE, compute_versions, model_version

ROOT = Path(__file__).resolve().parents[2]

POLICY = """---
doc_id: {doc_id}
title: Test policy
product: all
version: "{version}"
effective_date: 2026-01-01
audience: customer
---
# Test policy

Body text.
"""


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    prompts = tmp_path / "prompts"
    (prompts / "states").mkdir(parents=True)
    (prompts / "VERSION").write_text("1.2.0\n")
    (prompts / "greet.md").write_text("Hello, this is {agent_name}.\n")
    (prompts / "states" / "verify.yaml").write_text("tools: [verify_identity]\n")
    policies = tmp_path / "policies"
    policies.mkdir()
    for doc_id in ("late-fees", "faq"):
        (policies / f"{doc_id}.md").write_text(POLICY.format(doc_id=doc_id, version="1.0"))
    config = tmp_path / "config"
    config.mkdir()
    (config / "contact_rules.yaml").write_text("version: 3\nwindow: {start: '08:00', end: '21:00'}\n")
    (config / "disclosures.yaml").write_text("version: '2'\nai_recording: {text: hi}\n")
    return {"prompts_dir": prompts, "policies_dir": policies, "config_dir": config}


def _compute(settings: Settings, dirs: dict[str, Path]) -> Versions:
    return compute_versions(settings, **dirs)


def test_formula_shape(settings: Settings, dirs: dict[str, Path]) -> None:
    v = _compute(settings, dirs)
    assert v.prompt.startswith("1.2.0+") and len(v.prompt.split("+")[1]) == 8
    kb, rules, disc = v.policy.split(";")
    assert kb.startswith("kb:") and len(kb) == len("kb:") + 8
    assert rules == "rules:3"
    assert disc == "disc:2"
    llm = "llm:llamacpp/llama-3.1-8b-instruct"
    assert v.model == f"{llm};stt:small.en;tts:af_heart;embed:nomic-embed-text-v1.5"


def test_identical_inputs_identical_versions(settings: Settings, dirs: dict[str, Path]) -> None:
    assert _compute(settings, dirs) == _compute(settings, dirs)


def test_deterministic_across_processes(settings: Settings, dirs: dict[str, Path]) -> None:
    """A fresh interpreter with a different hash seed computes the same prompt and policy stamps."""
    code = (
        "import json, sys; from pathlib import Path; from supportagent.observability import versions as v;"
        "d = json.loads(sys.argv[1]);"
        "print(json.dumps([v.prompt_version(Path(d['prompts_dir'])),"
        " v.policy_version(Path(d['policies_dir']), Path(d['config_dir']))]))"
    )
    arg = json.dumps({k: str(p) for k, p in dirs.items()})
    out = subprocess.run(
        [sys.executable, "-c", code, arg],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "12345"},
        cwd=ROOT,
    ).stdout
    here = _compute(settings, dirs)
    assert json.loads(out) == [here.prompt, here.policy]


def test_file_order_and_mtime_do_not_matter(settings: Settings, dirs: dict[str, Path]) -> None:
    before = _compute(settings, dirs)
    greet = dirs["prompts_dir"] / "greet.md"
    os.utime(greet, (1, 1))  # mtime changes, content does not
    assert _compute(settings, dirs) == before


def test_changing_one_prompt_changes_only_prompt(settings: Settings, dirs: dict[str, Path]) -> None:
    before = _compute(settings, dirs)
    (dirs["prompts_dir"] / "states" / "verify.yaml").write_text("tools: [verify_identity, escalate]\n")
    after = _compute(settings, dirs)
    assert after.prompt != before.prompt
    assert after.prompt.startswith("1.2.0+")
    assert (after.policy, after.model) == (before.policy, before.model)


def test_adding_a_prompt_file_changes_prompt(settings: Settings, dirs: dict[str, Path]) -> None:
    before = _compute(settings, dirs)
    (dirs["prompts_dir"] / "closing.md").write_text("Goodbye.\n")
    assert _compute(settings, dirs).prompt != before.prompt


def test_non_prompt_files_are_ignored(settings: Settings, dirs: dict[str, Path]) -> None:
    before = _compute(settings, dirs)
    (dirs["prompts_dir"] / "notes.txt").write_text("scratch\n")
    (dirs["prompts_dir"] / "__pycache__").mkdir()
    (dirs["prompts_dir"] / "__pycache__" / "x.md").write_text("cache\n")
    assert _compute(settings, dirs) == before


def test_bumping_a_policy_version_changes_only_policy(settings: Settings, dirs: dict[str, Path]) -> None:
    before = _compute(settings, dirs)
    (dirs["policies_dir"] / "faq.md").write_text(POLICY.format(doc_id="faq", version="1.1"))
    after = _compute(settings, dirs)
    assert after.policy != before.policy
    assert after.policy.endswith(";rules:3;disc:2")
    assert (after.prompt, after.model) == (before.prompt, before.model)


def test_policy_body_edit_without_version_bump_keeps_policy(
    settings: Settings, dirs: dict[str, Path]
) -> None:
    """The policy stamp follows doc_id@version, as the formula says: content changes need a version bump."""
    before = _compute(settings, dirs)
    path = dirs["policies_dir"] / "faq.md"
    path.write_text(path.read_text() + "\nMore text.\n")
    assert _compute(settings, dirs).policy == before.policy


def test_rules_and_disclosure_versions_change_only_policy(settings: Settings, dirs: dict[str, Path]) -> None:
    before = _compute(settings, dirs)
    (dirs["config_dir"] / "contact_rules.yaml").write_text("version: 4\n")
    (dirs["config_dir"] / "disclosures.yaml").write_text("version: '3'\n")
    after = _compute(settings, dirs)
    assert after.policy.endswith(";rules:4;disc:3")
    assert (after.prompt, after.model) == (before.prompt, before.model)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("llm_provider", "anthropic", "llm:anthropic/llama-3.1-8b-instruct"),
        ("llm_model", "llama-3.2-3b-instruct", "llm:llamacpp/llama-3.2-3b-instruct"),
        ("whisper_model", "large-v3-turbo", "stt:large-v3-turbo"),
        ("tts_voice", "am_adam", "tts:am_adam"),
        ("embed_model", "mxbai-embed-large", "embed:mxbai-embed-large"),
    ],
)
def test_changing_model_settings_changes_only_model(
    settings: Settings, dirs: dict[str, Path], field: str, value: str, expected: str
) -> None:
    before = _compute(settings, dirs)
    changed = settings.model_copy(update={"models": settings.models.model_copy(update={field: value})})
    after = _compute(changed, dirs)
    assert expected in after.model
    assert after.model != before.model
    assert (after.prompt, after.policy) == (before.prompt, before.policy)


def test_model_version_matches_the_hosted_switch_format(settings: Settings) -> None:
    hosted = settings.models.model_copy(update={"llm_provider": "anthropic", "llm_model": "claude-haiku-5-5"})
    assert model_version(hosted).startswith("llm:anthropic/claude-haiku-5-5;")


def test_missing_inputs_give_none_components(settings: Settings, tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    v = compute_versions(settings, prompts_dir=missing, policies_dir=missing, config_dir=missing)
    assert v.prompt == f"{NONE}+{NONE}"
    assert v.policy == f"kb:{NONE};rules:{NONE};disc:{NONE}"
    assert v.model.startswith("llm:llamacpp/")


def test_missing_version_file_and_config_keys(settings: Settings, dirs: dict[str, Path]) -> None:
    (dirs["prompts_dir"] / "VERSION").unlink()
    (dirs["config_dir"] / "disclosures.yaml").unlink()
    (dirs["config_dir"] / "contact_rules.yaml").write_text("window: {start: '08:00'}\n")  # no version key
    v = _compute(settings, dirs)
    assert v.prompt.startswith(f"{NONE}+") and v.prompt != f"{NONE}+{NONE}"
    assert v.policy.endswith(f";rules:{NONE};disc:{NONE}")


def test_unreadable_or_malformed_inputs_never_raise(settings: Settings, dirs: dict[str, Path]) -> None:
    (dirs["config_dir"] / "contact_rules.yaml").write_text("version: [unclosed\n")
    (dirs["policies_dir"] / "broken.md").write_text("no front matter here\n")
    (dirs["policies_dir"] / "bad-yaml.md").write_text("---\ndoc_id: [x\n---\nbody\n")
    v = _compute(settings, dirs)
    assert ";rules:none;" in v.policy
    assert v.policy.startswith("kb:")


def test_real_repository_inputs_produce_a_stamp(settings: Settings) -> None:
    """Against the repo defaults (prompts and config may not exist yet in early milestones)."""
    v = compute_versions(settings)
    assert v.policy.startswith("kb:") and not v.policy.startswith(f"kb:{NONE}")  # data/policies exists
    assert v.prompt.count("+") == 1
    assert versions.DEFAULT_POLICIES_DIR == ROOT / "data" / "policies"
