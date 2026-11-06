"""M1 exit demo (specs/milestones.md): the gateway end to end, as the agent worker will use it.

Opens a gateway session for an outbound missed-payment call to Forgetful Frank, then through a real
MCP client: verifies him, reads his loan status, searches "late fee personal loan", records a promise
to pay and the call outcome — and finally shows the audit rows for the call.

Needs the services up, the bank seeded and the KB ingested:

    make up && docker compose up -d bank-api gateway
    docker compose exec bank-api python -m supportagent.bank.seed --as-of 2026-11-09 --reset
    make kb-ingest
    uv run python scripts/demo_m1.py
"""

import asyncio
import datetime as dt
import json
import sys
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

FRANK = {"loan_id": 9002, "full_name": "Frank Fischer", "dob": "1985-03-14", "loan_last4": "4417"}


@dataclass
class DemoResult:
    call_id: str
    steps: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def data(self, tool: str) -> dict[str, Any]:
        result = dict(self.steps)[tool]
        if not result["ok"]:
            raise RuntimeError(f"{tool} failed: {result['error']}")
        return dict(result["data"])


async def run_demo(gateway_url: str, worker_token: str, persona: dict[str, Any] = FRANK) -> DemoResult:
    """Run the M1 flow against a gateway at `gateway_url` (e.g. http://localhost:8002)."""
    call_id = str(uuid.uuid4())
    async with httpx.AsyncClient(base_url=gateway_url) as http:
        resp = await http.post(
            "/sessions",
            json={"call_id": call_id, "direction": "outbound", "campaign": "missed_payment",
                  "loan_id": persona["loan_id"]},
            headers={"Authorization": f"Bearer {worker_token}"},
        )  # fmt: skip
        resp.raise_for_status()
        token = resp.json()["session_token"]

    demo = DemoResult(call_id)
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=30) as mcp_http,
        Client(streamable_http_client(f"{gateway_url}/mcp", http_client=mcp_http)) as client,
    ):

        async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
            result = await client.call_tool(tool, args)
            payload = dict(result.structured_content or {})
            demo.steps.append((tool, payload))
            return payload

        claim = {k: persona[k] for k in ("full_name", "dob", "loan_last4")}
        await call("verify_identity", claim)
        await call("get_loan_status", {})
        loan = demo.data("get_loan_status")["loans"][0]
        await call("search_policy", {"query": "late fee personal loan", "product": "PERSONAL"})
        as_of = dt.date.fromisoformat(loan["oldest_unpaid_due_date"]) + dt.timedelta(
            days=loan["days_past_due"]
        )
        promise_date = as_of + dt.timedelta(days=3)
        amount = Decimal(loan["overdue_amount"])
        await call("record_promise_to_pay", {"promise_date": promise_date.isoformat(), "amount": str(amount)})
        outcome = {
            "call_id": call_id, "direction": "outbound", "campaign": "missed_payment",
            "customer_id": None, "loan_id": persona["loan_id"], "contact_result": "right_party",
            "verified": True, "disclosures_given": ["ai_recording@1.0", "collections@1.0"],
            "intent": ["promise_to_pay"], "reason_code": "forgot",
            "promise_to_pay": {"date": promise_date.isoformat(), "amount": str(amount)},
            "sentiment": "positive", "summary": "Forgot the payment; promised to pay in 3 days.",
            "versions": {"prompt": "demo", "policy": "demo", "model": "demo"},
        }  # fmt: skip
        await call("record_call_outcome", outcome)
    return demo


def _print(demo: DemoResult) -> None:
    print(f"call_id: {demo.call_id}")
    for tool, payload in demo.steps:
        status = "ok" if payload.get("ok") else f"ERROR {payload.get('error')}"
        print(f"\n== {tool}: {status}")
        print(json.dumps(payload.get("data"), indent=2)[:1200])


def _audit_rows(call_id: str) -> list[tuple[str, str, int]]:
    from sqlalchemy import text

    from supportagent.common.db import bank_engine

    with bank_engine().connect() as conn:
        rows = conn.execute(
            text("select tool, decision, latency_ms from audit_log where call_id = :c order by ts"),
            {"c": call_id},
        )
        return [(r[0], r[1], r[2]) for r in rows]


def main() -> int:
    from supportagent.common.settings import get_settings

    settings = get_settings()
    base = settings.services.gateway_url.removesuffix("/mcp").rstrip("/")
    demo = asyncio.run(run_demo(base, settings.services.gateway_worker_token.get_secret_value()))
    _print(demo)
    rows = _audit_rows(demo.call_id)
    print(f"\n== audit_log for {demo.call_id}: {len(rows)} rows")
    for tool, decision, latency in rows:
        print(f"  {tool:<24} {decision:<6} {latency} ms")
    ok = all(p.get("ok") for _, p in demo.steps) and len(rows) == len(demo.steps)
    print("\nM1 exit demo:", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
