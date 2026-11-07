"""COMP-R8: the versioned YAML config and the KB policy documents state the same rules.

The agent quotes the KB documents; the scheduler and the gate enforce the YAML. If either side changes
alone, these tests fail.
"""

import re
from pathlib import Path

import pytest

from supportagent.compliance.config import load_rules

ROOT = Path(__file__).resolve().parents[2]
POLICIES = ROOT / "data" / "policies"
RULES_DOC = POLICIES / "contact-calling-rules.md"


def _doc(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT)} is absent")
    return path.read_text()


def _version(doc: str) -> str:
    m = re.search(r'^version:\s*"?([\d.]+)"?\s*$', doc, re.M)
    assert m, "front matter has no version"
    return m.group(1)


def test_contact_rules_match_kb() -> None:
    doc = " ".join(_doc(RULES_DOC).split())  # collapse line wrapping
    rules = load_rules(ROOT / "data" / "config" / "contact_rules.yaml")
    w, f, r, c = rules.calling_window, rules.frequency, rules.retries, rules.campaigns
    expected = [
        f"configuration version {rules.version}",
        f"between {w.start:%H:%M} and {w.end:%H:%M} in the customer's local time zone",
        f"may start at {w.start:%H:%M} but not at or after {w.end:%H:%M}",
        f"No more than {f.max_attempts} call attempts in any {f.window_days} days for the same loan",
        f"no further collection call is placed for {f.cool_off_days_after_conversation} days",
        f"No answer: retry after {r.no_answer_retry_minutes} minutes",
        f"Busy line: retry after {r.busy_retry_minutes} minutes",
        f"Voicemail: retry after {r.voicemail_retry_minutes} minutes",
        f"No more than {r.max_attempts_per_day} call attempts per day",
        f"instalments due within the next {c.pre_due_reminder.due_within_days} days",
        f"loans {c.missed_payment.dpd_min} to {c.missed_payment.dpd_max} days past due",
    ]
    missing = [phrase for phrase in expected if phrase not in doc]
    assert missing == [], f"contact-calling-rules.md disagrees with contact_rules.yaml: {missing}"
    assert _version(_doc(RULES_DOC)) == rules.version
    for rule in ("do-not-call", "consent", "cease contact", "open dispute"):
        assert rule in doc, f"KB doc no longer mentions {rule!r}"


def test_consistency_check_catches_a_changed_window(tmp_path: Path) -> None:
    """COMP-R8: changing the window in YAML without updating the KB doc fails the check."""
    src = (ROOT / "data" / "config" / "contact_rules.yaml").read_text()
    changed = tmp_path / "contact_rules.yaml"
    changed.write_text(src.replace('start: "08:00"', 'start: "09:00"'))
    rules = load_rules(changed)
    doc = " ".join(_doc(RULES_DOC).split())
    assert f"between {rules.calling_window.start:%H:%M} and" not in doc
