"""T-PLAT-06 · PLAT-R7: `make doctor` lists every prerequisite and gives a fix hint for each failure."""

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "doctor.sh"
BASH = shutil.which("bash") or "/bin/bash"
SYSTEM_PATH = f"/usr/bin{os.pathsep}/bin"

REQUIRED = ["uv", "docker", "docker-daemon", "compose", "postgres-port"]
OPTIONAL = ["llama-server", "llm", "embed", "whisper", "kokoro", "livekit"]
HINT_COMMANDS = {"brew", "open", "update", "set", "llama-server", "git"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _env(path: str, tmp_path: Path, postgres_port: int | None = None) -> dict[str, str]:
    # Every server URL points at a closed port, so results never depend on what runs locally.
    return {
        "PATH": path,
        "HOME": str(tmp_path),
        "POSTGRES_PORT": str(postgres_port or _free_port()),
        "LLM_URL": f"http://127.0.0.1:{_free_port()}/v1",
        "EMBED_URL": f"http://127.0.0.1:{_free_port()}/v1",
        "STT_URL": f"http://127.0.0.1:{_free_port()}",
        "TTS_URL": f"http://127.0.0.1:{_free_port()}",
        "LIVEKIT_URL": f"ws://127.0.0.1:{_free_port()}",
    }


def _run(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def stub_path(tmp_path: Path) -> str:
    """A PATH with working uv/docker stubs, so every required check passes deterministically."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "uv", 'echo "uv 0.9.0"')
    _stub(
        bin_dir,
        "docker",
        'case "$1" in\n'
        '  --version) echo "Docker version 29.0.0" ;;\n'
        '  info) echo "server 29.0.0" ;;\n'
        '  compose) [ "$2" = version ] && echo "2.40.0" ;;\n'
        "esac\nexit 0",
    )
    return f"{bin_dir}{os.pathsep}{SYSTEM_PATH}"


def test_dry_run_lists_every_check(tmp_path: Path) -> None:
    res = _run("--dry-run", env=_env(os.environ["PATH"], tmp_path))
    assert res.returncode == 0, res.stderr
    kinds = {line.split()[0]: line.split()[1] for line in res.stdout.splitlines() if line.strip()}
    assert {k: kinds.get(k) for k in REQUIRED} == dict.fromkeys(REQUIRED, "required")
    assert {k: kinds.get(k) for k in OPTIONAL} == dict.fromkeys(OPTIONAL, "optional")
    assert "✓" not in res.stdout
    assert "✗" not in res.stdout  # nothing was probed


def test_required_pass_optional_missing_exits_zero(stub_path: str, tmp_path: Path) -> None:
    res = _run(env=_env(stub_path, tmp_path))
    assert res.returncode == 0, res.stdout
    for check in REQUIRED:
        assert f"✓ {check}:" in res.stdout
    assert "✗ llm: not reachable" in res.stdout
    assert "(optional until M2) → llama-server -m " in res.stdout
    assert "--alias llama-3.1-8b-instruct --jinja" in res.stdout
    assert "--embedding --pooling mean" in res.stdout


def test_strict_fails_on_optional(stub_path: str, tmp_path: Path) -> None:
    assert _run("--strict", env=_env(stub_path, tmp_path)).returncode == 1


def test_missing_required_tool_fails_with_install_hint(tmp_path: Path) -> None:
    res = _run(env=_env(SYSTEM_PATH, tmp_path))  # no uv on this PATH
    assert res.returncode == 1
    assert "✗ uv: not found on PATH → brew install uv" in res.stdout


@pytest.fixture
def bare_path(tmp_path: Path) -> str:
    """A PATH with only the shell utilities the script needs: no uv, docker or curl anywhere."""
    bin_dir = tmp_path / "bare"
    bin_dir.mkdir()
    for tool in ("dirname", "printenv", "grep", "tail", "head", "cut", "sed"):
        found = shutil.which(tool)
        assert found, tool
        (bin_dir / tool).symlink_to(found)
    return str(bin_dir)


# postgres-port passes when the port is free; its failure hint is covered by the occupied-port test.
@pytest.mark.parametrize("check", [c for c in REQUIRED + OPTIONAL if c != "postgres-port"])
def test_every_failure_line_names_a_command(check: str, bare_path: str, tmp_path: Path) -> None:
    res = _run(env=_env(bare_path, tmp_path))
    assert res.returncode == 1
    line = next(ln for ln in res.stdout.splitlines() if ln.startswith(f"✗ {check}:"))
    hint = line.split(" → ", 1)[1]
    assert hint.split()[0] in HINT_COMMANDS


def test_occupied_postgres_port_is_reported(stub_path: str, tmp_path: Path) -> None:
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = int(srv.getsockname()[1])
        res = _run(env=_env(stub_path, tmp_path, postgres_port=port))
    assert res.returncode == 1
    assert f"✗ postgres-port: port {port} is used by another process" in res.stdout
    assert "set POSTGRES_PORT=" in res.stdout


def test_unknown_option(tmp_path: Path) -> None:
    assert _run("--bogus", env=_env(os.environ["PATH"], tmp_path)).returncode == 2
