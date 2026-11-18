"""T-SCHED-01 · SCHED-R1: the service-scope gateway client sends the bearer token and parses the contracts."""

import json
from collections.abc import Callable

import httpx
import pytest

from supportagent.contracts.gateway import CallAttempt
from supportagent.scheduler.gateway_client import GatewayServiceClient, ServiceCallError
from tests.scheduler.conftest import NOW, frank

TOKEN = "service-token-for-tests"


def client(handler: Callable[[httpx.Request], httpx.Response]) -> GatewayServiceClient:
    return GatewayServiceClient(
        "http://gateway.test", TOKEN, transport=httpx.MockTransport(handler), timeout_s=5.0
    )


@pytest.mark.asyncio
async def test_candidates_send_the_bearer_token_and_parse_the_contract() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[json.loads(frank().model_dump_json())])

    got = await client(handler).candidates("missed_payment")
    assert [(c.customer_id, c.loan_id) for c in got] == [(1, 11)]
    req = seen[0]
    assert req.url.path == "/service/candidates" and req.url.params["campaign"] == "missed_payment"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"


@pytest.mark.asyncio
async def test_contact_preferences_are_read_for_one_customer() -> None:
    body = {
        "consent_voice_ai": True,
        "dnc": True,
        "cease_contact": False,
        "dispute_open": False,
        "preferred_window": None,
    }
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json=body)

    prefs = await client(handler).contact_preferences(7)
    assert prefs.dnc is True and paths == ["/service/contact-preferences/7"]


@pytest.mark.asyncio
async def test_an_attempt_is_posted_and_the_interaction_id_returned() -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"interaction_id": sent[0]["call_id"]})

    attempt = CallAttempt(
        call_id="00000000-0000-4000-8000-000000000001",
        customer_id=1,
        loan_id=11,
        campaign="missed_payment",
        contact_result="no_answer",
        started_at=NOW,
    )
    recorded = await client(handler).record_attempt(attempt)
    assert recorded.interaction_id == attempt.call_id
    assert sent[0]["campaign"] == "missed_payment"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 422, 503])
async def test_an_error_status_is_a_service_call_error_without_the_token(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "nope"})

    with pytest.raises(ServiceCallError) as err:
        await client(handler).candidates("pre_due")
    assert str(status) in str(err.value) and TOKEN not in str(err.value)


@pytest.mark.asyncio
async def test_a_connection_failure_is_a_service_call_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ServiceCallError, match="unreachable"):
        await client(handler).candidates("pre_due")


@pytest.mark.asyncio
async def test_a_malformed_body_is_a_service_call_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"customer_id": "x"}])

    with pytest.raises(ServiceCallError, match="unexpected response"):
        await client(handler).candidates("pre_due")
