"""Record the Next.js console's e2e fixtures from the real console API (T-CON-10, CON-R11).

    uv run python -m tests.console.web_fixtures      # rewrite web/console/tests/e2e/fixtures/

Each fixture is one console API exchange, produced by `console.api.create_app` over the persona-seeded
fake bank and fake gateway (the same fakes as the pytest suite). The Playwright fake server
(`web/console/tests/e2e/fake-console-api.mjs`) replays them, so the browser tests see exactly what the
console API returns. `test_web_fixtures.py` fails when they drift from the API.

A fixture matches a request by method and path, then by `query` (a subset of the request's query
string, the most specific match wins) or, for POST, by `body` (a subset of the JSON body).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from supportagent.console.api import create_app
from tests.console.conftest import CALL_ID, FakeApi, make_clients

OUT = Path(__file__).resolve().parents[2] / "web" / "console" / "tests" / "e2e" / "fixtures"
TOKEN = "fixture-recorder"


@dataclass(frozen=True)
class Exchange:
    name: str
    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None
    fail: dict[str, int] = field(default_factory=dict)  # upstream failures to simulate (FakeApi.fail)


# Everything the M2 pages (T-CON-10..13) request. Add an exchange here, re-run, commit the fixtures.
EXCHANGES = [
    Exchange("overview", "GET", "/api/overview"),
    Exchange("customers-default", "GET", "/api/customers"),
    Exchange("customers-frank", "GET", "/api/customers", {"q": "frank"}),
    Exchange("customers-none", "GET", "/api/customers", {"q": "zz-nobody"}),
    Exchange("customers-bucket-1-30", "GET", "/api/customers", {"bucket": "1-30"}),
    Exchange("customer-frank", "GET", "/api/customers/9002"),
    Exchange("customer-sam", "GET", "/api/customers/9007"),
    Exchange("customer-missing", "GET", "/api/customers/424242"),
    Exchange("loans-default", "GET", "/api/loans"),
    Exchange("policies", "GET", "/api/policies"),
    Exchange("policy-late-fees", "GET", "/api/policies/late-fees-penalty-interest"),
    Exchange("policy-late-fees-1-0", "GET", "/api/policies/late-fees-penalty-interest", {"version": "1.0"}),
    Exchange("policy-fees-schedule", "GET", "/api/policies/fees-schedule"),
    Exchange("policy-calling-rules", "GET", "/api/policies/contact-calling-rules"),
    Exchange(
        "policy-broken",
        "GET",
        "/api/policies/fees-schedule",
        {"version": "9.9"},
        fail={"/service/kb/documents/fees-schedule": 500},
    ),
    Exchange("policy-missing", "GET", "/api/policies/no-such-doc"),
    Exchange("search-late-fee", "POST", "/api/policies/search", body={"query": "late fee personal loan"}),
    Exchange("search-france", "POST", "/api/policies/search", body={"query": "capital of France"}),
    Exchange("personas", "GET", "/api/personas"),
    Exchange("audit-demo-call", "GET", f"/api/audit/{CALL_ID}"),
    Exchange("audit-unknown", "GET", "/api/audit/no-such-call"),
]


def record(exchange: Exchange) -> dict[str, Any]:
    api = FakeApi()
    api.fail.update(exchange.fail)
    client = TestClient(create_app(make_clients(api), token=TOKEN))
    response = client.request(
        exchange.method,
        exchange.path,
        params=exchange.query,
        json=exchange.body,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    return {
        "name": exchange.name,
        "method": exchange.method,
        "path": exchange.path,
        "query": exchange.query,
        "body": exchange.body,
        "status": response.status_code,
        "response": response.json(),
    }


def render_all() -> dict[str, str]:
    """File name → file text for every exchange."""
    out = {}
    for exchange in EXCHANGES:
        assert re.fullmatch(r"[a-z0-9-]+", exchange.name), exchange.name
        out[f"{exchange.name}.json"] = json.dumps(record(exchange), indent=2, ensure_ascii=False) + "\n"
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = render_all()
    for stale in set(p.name for p in OUT.glob("*.json")) - set(files):
        (OUT / stale).unlink()
    for name, text in files.items():
        (OUT / name).write_text(text)
    print(f"wrote {len(files)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
