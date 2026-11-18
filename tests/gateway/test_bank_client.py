"""T-GW-01: `HttpBankClient` against `httpx.MockTransport` (no network)."""

import datetime as dt
import json
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from supportagent.contracts import bank as c
from supportagent.gateway.bank_client import TIMEOUT_S, BankError, HttpBankClient
from tests.gateway.fakes import FRANK, FakeBank

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> HttpBankClient:
    return HttpBankClient("http://bank", "tok", transport=httpx.MockTransport(handler))


def _json(model: object) -> httpx.Response:
    from pydantic import TypeAdapter

    return httpx.Response(200, content=TypeAdapter(type(model)).dump_json(model))


BANK = FakeBank()


async def test_reads_build_urls_params_and_parse_dtos() -> None:
    seen: list[httpx.Request] = []
    status = BANK.status_of(FRANK.loan_id)

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.headers["X-Service-Token"] == "tok"
        path = req.url.path
        if path == "/customers":
            return _json([BANK.customers[FRANK.customer_id]])
        if path == f"/customers/{FRANK.customer_id}/loans":
            return _json([BANK.loans[FRANK.loan_id]])
        if path == f"/loans/{FRANK.loan_id}":
            return _json(status)
        if path.startswith("/accounts/"):
            return _json([])
        if path.endswith("/interactions"):
            return _json(c.InteractionHistory(interactions=[], open_promises=[], open_tasks=[]))
        return httpx.Response(404)

    client = _client(handler)
    assert [cu.id for cu in await client.customers_by_phone(FRANK.phone)] == [FRANK.customer_id]
    assert (await client.customer_loans(FRANK.customer_id))[0].number.endswith(FRANK.loan_last4)
    got = await client.loan_status(FRANK.loan_id)
    assert got == status and got.days_past_due == 5
    assert await client.transactions(9002, loan_id=9002, limit=5) == []
    assert (await client.interaction_history(9002, limit=10)).interactions == []
    assert await client.get_customer(1) is None  # 404 → None
    params = {r.url.path: dict(r.url.params) for r in seen}
    assert params["/customers"] == {"phone": FRANK.phone}
    assert params["/accounts/9002/transactions"] == {"limit": "5", "loan_id": "9002"}
    assert params["/customers/9002/interactions"] == {"limit": "10"}
    await client.aclose()


async def test_get_is_retried_once_then_fails() -> None:
    attempts = 0

    def flaky(req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("boom")
        return _json(BANK.status_of(FRANK.loan_id))

    assert await _client(flaky).loan_status(FRANK.loan_id) is not None
    assert attempts == 2

    attempts = 0

    def down(req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    with pytest.raises(BankError):
        await _client(down).loan_status(FRANK.loan_id)
    assert attempts == 2


async def test_writes_are_never_retried() -> None:
    attempts = 0

    def down(req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("boom")

    body = c.PromiseIn(loan_id=9002, promised_date=dt.date(2026, 11, 12), amount=Decimal("10"))
    with pytest.raises(BankError):
        await _client(down).create_promise(body)
    assert attempts == 1


async def test_writes_send_json_and_patch_only_set_fields() -> None:
    sent: dict[str, object] = {}
    prefs = BANK.prefs[FRANK.customer_id]

    def handler(req: httpx.Request) -> httpx.Response:
        sent[f"{req.method} {req.url.path}"] = json.loads(req.content)
        assert req.headers["content-type"] == "application/json"
        if req.url.path == "/audit":
            return httpx.Response(204)
        if req.url.path.startswith("/contact-preferences"):
            return _json(prefs)
        return httpx.Response(500)

    client = _client(handler)
    await client.patch_contact_preferences(9002, c.ContactPreferencesPatch(dispute_open=True))
    assert sent["PATCH /contact-preferences/9002"] == {"dispute_open": True}
    audit = c.AuditIn(
        ts=dt.datetime(2026, 11, 9, tzinfo=dt.UTC), call_id="x", tool="t", args_hash="0" * 64,
        decision="allow", latency_ms=1,
    )  # fmt: skip
    await client.append_audit(audit)
    with pytest.raises(BankError) as exc:
        await client.create_task(
            c.TaskIn(
                customer_id=9002, type="callback", reason="r", due_at=dt.datetime(2026, 11, 10, tzinfo=dt.UTC)
            )
        )
    assert exc.value.status == 500


def test_default_timeout() -> None:
    assert TIMEOUT_S == 2.0
    client = HttpBankClient("http://bank", "tok")
    assert client._client.timeout.read == 2.0


async def test_fake_bank_matches_ledger() -> None:
    bank = FakeBank()
    status = await bank.loan_status(FRANK.loan_id)
    assert status is not None and status.days_past_due == 5 and status.number_last4 == FRANK.loan_last4


# --------------------------------------------------------------------------- scheduler reads (T-GW-10)


async def test_eligible_loans_sends_exactly_one_selector_and_parses_rows() -> None:
    seen: list[dict[str, str]] = []
    rows = await FakeBank().eligible_loans(bucket="1-30")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/collections/eligible"
        seen.append(dict(req.url.params))
        return _json(rows)

    client = _client(handler)
    assert [r.loan_id for r in await client.eligible_loans(bucket="1-30")] == [r.loan_id for r in rows]
    await client.eligible_loans(due_within=3)
    assert seen == [{"bucket": "1-30"}, {"due_within": "3"}]


async def test_contact_preferences_parse_and_404_is_none() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == f"/customers/{FRANK.customer_id}/contact-preferences":
            return _json(BANK.prefs[FRANK.customer_id])
        return httpx.Response(404)

    client = _client(handler)
    prefs = await client.contact_preferences(FRANK.customer_id)
    assert prefs is not None and prefs.customer_id == FRANK.customer_id
    assert await client.contact_preferences(424242) is None


async def test_tasks_are_read_page_by_page_with_their_filters() -> None:
    when = dt.datetime(2026, 11, 9, tzinfo=dt.UTC)
    task = c.Task(
        id=1,
        call_id=None,
        customer_id=FRANK.customer_id,
        loan_id=None,
        type="callback",
        reason="r",
        summary="",
        due_at=when,
        owner=None,
        status="open",
        created_at=when,
    )
    seen: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.url.params))
        offset = int(req.url.params["offset"])
        items = [task.model_copy(update={"id": offset + 1})] if offset < 2 else []
        return _json(c.Page[c.Task](items=items, total=2, limit=200, offset=offset))

    tasks = await _client(handler).list_tasks(status="open", task_type="callback")
    assert [t.id for t in tasks] == [1, 2]
    assert seen[0] == {"status": "open", "type": "callback", "limit": "200", "offset": "0"}
    assert seen[1]["offset"] == "1"


async def test_promises_filter_by_status() -> None:
    seen: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/promises"
        seen.append(dict(req.url.params))
        return _json(c.Page[c.Promise](items=[], total=0, limit=200, offset=0))

    assert await _client(handler).list_promises(status="broken") == []
    assert seen[0]["status"] == "broken"
