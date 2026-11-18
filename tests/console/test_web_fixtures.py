"""T-CON-10 · CON-R11: the Next.js e2e fixtures are exactly what the console API returns."""

import json

from tests.console.web_fixtures import EXCHANGES, OUT, render_all


def test_committed_fixtures_match_the_console_api() -> None:
    expected = render_all()
    committed = {p.name: p.read_text() for p in OUT.glob("*.json")}
    assert set(committed) == set(expected), "run `uv run python -m tests.console.web_fixtures`"
    stale = [name for name, text in expected.items() if committed[name] != text]
    assert stale == [], f"stale fixtures {stale}: run `uv run python -m tests.console.web_fixtures`"


def test_fixtures_cover_errors_and_masking() -> None:
    files = render_all()
    by_name = {e.name: json.loads(files[f"{e.name}.json"]) for e in EXCHANGES}
    assert by_name["customer-missing"]["status"] == 404
    assert by_name["policy-broken"]["status"] == 502
    frank = by_name["customer-frank"]["response"]
    assert frank["loans"][0]["number"] == "•••• 4417"
    assert "dob" not in json.dumps(frank)
