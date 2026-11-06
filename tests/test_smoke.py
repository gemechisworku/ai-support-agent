import importlib

import pytest

import supportagent

MODULES = [
    "common",
    "contracts",
    "bank",
    "kb",
    "gateway",
    "compliance",
    "agent",
    "voice",
    "telephony",
    "scheduler",
    "observability",
    "console",
    "evals",
]


def test_version() -> None:
    assert supportagent.__version__


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    importlib.import_module(f"supportagent.{name}")
