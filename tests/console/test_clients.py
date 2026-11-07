"""T-CON-01/07 · CON-R1: typed clients — paths, params, auth headers, pagination and error mapping."""

import httpx
import pytest

from supportagent.console import clients as cl
from supportagent.contracts import bank as c
from supportagent.contracts.kb import PolicyQuery
from tests.console.conftest import BANK_URL, CALL_ID, GATEWAY_URL, TOKEN, FakeApi, make_clients


def _last(api: FakeApi) -> httpx.Request:
    return api.requests[-1]


def test_search_customers_sends_filters_and_token_and_parses_page() -> None:
    api = FakeApi()
    clients = make_clients(api)
    page = clients.bank.search_customers("frank", segment=None, bucket="1-30", limit=10, offset=0)
    req = _last(api)
    assert req.url.path == "/customers/search"
    assert dict(req.url.params) == {"q": "frank", "bucket": "1-30", "limit": "10", "offset": "0"}
    assert req.headers["x-service-token"] == TOKEN
    assert isinstance(page, c.Page)
    assert [s.customer_id for s in page.items] == [9002]
    assert page.total == 1


def test_empty_filters_are_not_sent() -> None:
    api = FakeApi()
    make_clients(api).bank.search_customers("", segment="", bucket=None)
    assert dict(_last(api).url.params) == {"limit": "50", "offset": "0"}


def test_pagination() -> None:
    api = FakeApi()
    clients = make_clients(api)
    first = clients.bank.search_customers(limit=5, offset=0)
    second = clients.bank.search_customers(limit=5, offset=5)
    assert first.total == second.total == 12
    assert {s.customer_id for s in first.items}.isdisjoint({s.customer_id for s in second.items})


def test_read_endpoints_return_contract_models() -> None:
    clients = make_clients(FakeApi())
    assert clients.bank.customer(9002).full_name
    assert clients.bank.loan_status(9002).days_past_due == 5
    assert clients.bank.installments(9002)[0].seq == 1
    assert isinstance(clients.bank.contact_preferences(9002), c.ContactPreferences)
    assert len(clients.bank.personas()) == 12
    rows = clients.bank.audit(CALL_ID).items
    assert next(r.tool for r in rows) == "verify_identity"


def test_patch_task_sends_only_set_fields() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": 7, "call_id": None, "customer_id": 9002, "loan_id": None, "type": "callback",
                "reason": "x", "summary": "", "due_at": "2026-11-09T14:00:00Z", "owner": None,
                "status": "done", "created_at": "2026-11-09T14:00:00Z",
            },
        )  # fmt: skip

    client = cl.BankOpsClient(BANK_URL, TOKEN, httpx.MockTransport(handler))
    task = client.patch_task(7, c.TaskPatch(status="done"))
    assert seen[0].method == "PATCH"
    assert seen[0].url.path == "/tasks/7"
    assert seen[0].content == b'{"status":"done"}'
    assert task.status == "done"


@pytest.mark.parametrize("status", [401, 404, 500])
def test_http_errors_become_console_api_errors(status: int) -> None:
    api = FakeApi(fail={"/loans": status})
    with pytest.raises(cl.ConsoleApiError, match=f"HTTP {status}"):
        make_clients(api).bank.loan_status(9002)


def test_unreachable_service_is_a_console_api_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = cl.BankOpsClient(BANK_URL, TOKEN, httpx.MockTransport(boom))
    with pytest.raises(cl.ConsoleApiError, match="not reachable"):
        client.personas()


def test_unexpected_payload_is_a_console_api_error() -> None:
    client = cl.BankOpsClient(
        BANK_URL, TOKEN, httpx.MockTransport(lambda r: httpx.Response(200, json={"x": 1}))
    )
    with pytest.raises(cl.ConsoleApiError, match="unexpected response"):
        client.loan_status(1)


def test_gateway_client_paths_auth_and_models() -> None:
    api = FakeApi()
    gw = make_clients(api).gateway
    docs = gw.kb_documents()
    assert _last(api).headers["authorization"] == f"Bearer {TOKEN}"
    assert _last(api).url.path == "/service/kb/documents"
    assert {d.doc_id for d in docs} >= {"fees-schedule"}
    detail = gw.kb_document("late-fees-penalty-interest", version="1.0")
    assert dict(_last(api).url.params) == {"version": "1.0"}
    assert len(detail.chunks) == 2
    result = gw.kb_search(PolicyQuery(query="late fee personal loan"))
    req = _last(api)
    assert req.method == "POST"
    assert req.url.path == "/service/kb/search"
    assert result.confident
    assert result.chunks[0].citation == "fees-schedule@1.0#Schedule of Loan Fees"


def test_gateway_client_error_mapping() -> None:
    api = FakeApi(fail={"/service/kb": 503})
    with pytest.raises(cl.ConsoleApiError, match="HTTP 503"):
        make_clients(api).gateway.kb_documents()


def test_gateway_base_url_strips_mcp() -> None:
    assert cl.gateway_base_url("http://gateway:8002/mcp") == "http://gateway:8002"
    assert cl.gateway_base_url("http://gateway:8002/") == "http://gateway:8002"
    assert cl.gateway_base_url(GATEWAY_URL + "/mcp") == GATEWAY_URL


def test_scheduler_is_a_stub_until_m3() -> None:
    sched = make_clients(FakeApi()).scheduler
    assert sched.available is False
    with pytest.raises(cl.ConsoleApiError, match="M3"):
        sched.active_calls()


def test_build_clients_uses_settings(settings: object) -> None:
    built = cl.build_clients()
    assert built.bank.base_url == "http://localhost:8001"
    assert built.gateway.base_url == "http://localhost:8002"


def test_get_clients_prefers_injected() -> None:
    injected = make_clients(FakeApi())
    cl.set_clients(injected)
    try:
        assert cl.get_clients() is injected
    finally:
        cl.set_clients(None)
