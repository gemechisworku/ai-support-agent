"""T-CON-09 · CON-R11: the committed OpenAPI document matches the console API (the web app's contract)."""

from pathlib import Path

from scripts.export_console_openapi import OUT, main, render


def test_committed_openapi_is_current() -> None:
    assert OUT.read_text() == render(), "web/console/openapi.json is stale: run `make console-types`"


def test_check_mode_flags_a_stale_file(tmp_path: Path) -> None:
    stale = tmp_path / "openapi.json"
    stale.write_text("{}\n")
    assert main(["--check", "--out", str(stale)]) == 1
    assert main(["--out", str(stale)]) == 0
    assert main(["--check", "--out", str(stale)]) == 0


def test_money_is_an_exact_string() -> None:
    import json

    schemas = json.loads(render())["components"]["schemas"]
    assert schemas["LoanRow"]["properties"]["overdue_amount"]["type"] == "string"
