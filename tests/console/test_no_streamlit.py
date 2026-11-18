"""T-CON-14 · CON-R1/R11: the console is the Next.js app + console API; Streamlit is gone for good.

Constitution VIII: every admin or console UI is a Next.js app under `web/`; no Python UI frameworks.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "src" / "supportagent" / "console"
PYTHON_UI_FRAMEWORKS = ("streamlit", "gradio", "dash", "nicegui", "panel", "flet")


def test_no_python_ui_framework_is_a_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    deps = [d.lower() for d in project["dependencies"]]
    deps += [d.lower() for group in project.get("optional-dependencies", {}).values() for d in group]
    assert [d for d in deps if d.startswith(PYTHON_UI_FRAMEWORKS)] == []
    assert 'name = "streamlit"' not in (ROOT / "uv.lock").read_text()


def test_console_package_has_no_streamlit_code() -> None:
    for path in CONSOLE.rglob("*.py"):
        assert "streamlit" not in path.read_text().lower(), path
    assert sorted(p.name for p in CONSOLE.glob("*.py")) == ["__init__.py", "api.py", "clients.py", "views.py"]


def test_console_api_imports_no_ui_framework() -> None:
    code = (
        "import sys, supportagent.console.api as api; api.create_app(token='x'); "
        f"print(sorted(m for m in {PYTHON_UI_FRAMEWORKS!r} if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_the_ui_is_the_nextjs_app() -> None:
    package = (ROOT / "web" / "console" / "package.json").read_text()
    assert '"next"' in package
