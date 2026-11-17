"""Live checks against the real native model servers (`make models-up`). Marker `live`: never in CI."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.live


def test_servers_healthy() -> None:
    """T-VOICE-01 · VOICE-R1: every native server answers health.py."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "models" / "health.py")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
