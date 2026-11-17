"""T-VOICE-05 · VOICE-R7: `scripts/playground.sh --dry-run` (the hosted playground is being retired).

The script clones LiveKit's open-source playground into vendor/ and runs it on the loopback address. The dry
run touches no network and writes nothing, so these tests only read its plan.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "playground.sh"
SECRET = "do-not-print-this-secret"


def _dry_run(directory: Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {
        "PLAYGROUND_DIR": str(directory),  # never the developer's real clone
        "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
        "LIVEKIT_API_KEY": "devkey",
        "LIVEKIT_API_SECRET": SECRET,
        "LIVEKIT_URL": "ws://127.0.0.1:7880",
    }
    return subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_dry_run_plans_a_pinned_clone_and_a_loopback_dev_server(tmp_path: Path) -> None:
    res = _dry_run(tmp_path / "pg")
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    assert "https://github.com/livekit/agents-playground.git" in out
    assert "fetch -q --depth 1 origin 2cfc4ac2257dabb4385f8a436e65cfd8a739d357" in out  # pinned, not main
    assert "npm install" in out
    assert "-p '3100' -H 127.0.0.1" in out  # loopback only; 3100 leaves 3000 to Langfuse
    assert "paste  ws://127.0.0.1:7880" in out
    assert "make dev-token" in out


def test_dry_run_never_prints_the_secret_and_changes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "pg"
    res = _dry_run(target)
    assert SECRET not in res.stdout + res.stderr
    assert "would write" in res.stdout  # the env file is planned, not written
    assert not target.exists()


def test_port_and_ref_are_settings(tmp_path: Path) -> None:
    res = _dry_run(tmp_path / "pg", PLAYGROUND_PORT="3201", PLAYGROUND_REF="abc123")
    assert "-p '3201' -H 127.0.0.1" in res.stdout
    assert "origin abc123" in res.stdout


def test_unknown_option_is_a_usage_error() -> None:
    res = subprocess.run(
        ["bash", str(SCRIPT), "--nope"], capture_output=True, text=True, timeout=30, check=False
    )
    assert res.returncode == 2
