"""T-VOICE-01 · VOICE-R1: scripts/models/health.py against stub HTTP servers (no real model servers)."""

import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HEALTH = ROOT / "scripts" / "models" / "health.py"
LLM_MODEL = "llama-3.1-8b-instruct"
EMBED_MODEL = "nomic-embed-text-v1.5"


@dataclass
class Stub:
    """One fake server: path → (status, JSON body)."""

    routes: dict[str, tuple[int, object]] = field(default_factory=dict)
    server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self.server
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> None:
        routes = self.routes

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                status, body = routes.get(self.path, (404, {"error": "not found"}))
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()


def llama_routes(model: str) -> dict[str, tuple[int, object]]:
    return {
        "/v1/models": (200, {"object": "list", "data": [{"id": model, "object": "model"}]}),
        "/health": (200, {"status": "ok"}),
    }


@pytest.fixture
def stubs() -> Iterator[dict[str, Stub]]:
    servers = {
        "llm": Stub(llama_routes(LLM_MODEL)),
        "embed": Stub(llama_routes(EMBED_MODEL)),
        "whisper": Stub({"/": (200, {"ok": True})}),
        "kokoro": Stub({"/v1/models": (200, {"data": [{"id": "kokoro"}]})}),
        "livekit": Stub({"/": (200, "OK")}),
    }
    for s in servers.values():
        s.start()
    yield servers
    for s in servers.values():
        s.stop()


def run_health(stubs: dict[str, Stub], tmp_path: Path, *names: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "MODELS_ENV_FILE": str(tmp_path / "missing.env"),  # ignore the developer's .env
        "MODELS_HEALTH_TIMEOUT_S": "2",
        "LLM_URL": f"{stubs['llm'].url}/v1",
        "EMBED_URL": f"{stubs['embed'].url}/v1",
        "LLM_MODEL": LLM_MODEL,
        "EMBED_MODEL": EMBED_MODEL,
        "STT_URL": stubs["whisper"].url,
        "TTS_URL": stubs["kokoro"].url,
        "LIVEKIT_URL": stubs["livekit"].url.replace("http://", "ws://"),
    }
    return subprocess.run(
        [sys.executable, str(HEALTH), *names], env=env, capture_output=True, text=True, timeout=30
    )


def test_all_healthy_exits_0(stubs: dict[str, Stub], tmp_path: Path) -> None:
    result = run_health(stubs, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5/5 healthy" in result.stdout
    for name in ("llm", "embed", "whisper", "kokoro", "livekit"):
        assert f"✓ {name}" in result.stdout


def test_a_server_down_names_it(stubs: dict[str, Stub], tmp_path: Path) -> None:
    stubs["kokoro"].stop()
    result = run_health(stubs, tmp_path)
    assert result.returncode == 1
    assert "✗ kokoro" in result.stdout and "unreachable" in result.stdout
    assert "failing: kokoro" in result.stdout
    assert "✓ llm" in result.stdout


def test_a_missing_model_names_it(stubs: dict[str, Stub], tmp_path: Path) -> None:
    stubs["llm"].routes.update(llama_routes("some-other-model"))
    result = run_health(stubs, tmp_path)
    assert result.returncode == 1
    assert "✗ llm" in result.stdout
    assert f"model '{LLM_MODEL}' not served (got some-other-model)" in result.stdout


def test_an_unhealthy_llama_server_fails(stubs: dict[str, Stub], tmp_path: Path) -> None:
    stubs["embed"].routes["/health"] = (503, {"error": "Loading model"})
    result = run_health(stubs, tmp_path, "embed")
    assert result.returncode == 1
    assert "HTTP 503" in result.stdout
    assert "0/1 healthy; failing: embed" in result.stdout


def test_only_the_named_servers_are_probed(stubs: dict[str, Stub], tmp_path: Path) -> None:
    stubs["whisper"].stop()
    result = run_health(stubs, tmp_path, "llm", "embed")
    assert result.returncode == 0
    assert "2/2 healthy" in result.stdout and "whisper" not in result.stdout


def test_unknown_server_is_a_usage_error(stubs: dict[str, Stub], tmp_path: Path) -> None:
    assert run_health(stubs, tmp_path, "ollama").returncode == 2


def test_env_file_values_are_used(tmp_path: Path) -> None:
    sys.path.insert(0, str(HEALTH.parent))
    try:
        import health  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    env = tmp_path / ".env"
    env.write_text("LLM_URL=http://localhost:9999/v1    # comment\n# LLM_MODEL=x\nTTS_URL=\n")
    values = health.read_env_file(env)
    assert values["LLM_URL"] == "http://localhost:9999/v1"
    assert "LLM_MODEL" not in values
    assert health.setting("TTS_URL", values) == "http://127.0.0.1:8880"
    assert health.root_of("http://127.0.0.1:8090/v1/") == "http://127.0.0.1:8090"
