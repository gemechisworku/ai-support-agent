"""T-GW-02 · GW-R2: the shared tool pipeline — session resolution and the scope matrix."""

import json

import pytest
from pydantic import BaseModel, ConfigDict

from supportagent.gateway import session as session_mod
from supportagent.gateway.bank_client import BankError
from supportagent.gateway.session import Session
from supportagent.gateway.tools import Gateway, Scope, ToolContext, ToolDenied, ToolRegistry, run_tool
from tests.gateway.conftest import open_session
from tests.gateway.fakes import FRANK


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")
    word: str = "hi"


class Out(BaseModel):
    echo: str


REG = ToolRegistry()
for _scope in ("unverified", "verified", "any", "worker"):

    async def _handler(ctx: ToolContext, inp: In) -> Out:
        if inp.word == "deny":
            raise ToolDenied("not_found")
        if inp.word == "bank":
            raise BankError("down", 500)
        return Out(echo=inp.word)

    REG.tool(_scope, scope=_scope, input_model=In, output_model=Out)(_handler)  # type: ignore[arg-type]


async def _state(gw: Gateway, state: str) -> Session:
    s = await open_session(gw, loan_id=FRANK.loan_id)
    if state == "bound":
        session_mod.bind_customer(s, FRANK.customer_id)
    elif state == "locked":
        s.locked = True
    elif state == "closed":
        s.closed = True
    return s


EXPECTED: dict[tuple[Scope, str], str | None] = {
    ("unverified", "unbound"): None, ("unverified", "bound"): None,
    ("unverified", "locked"): "locked", ("unverified", "closed"): "session_closed",
    ("verified", "unbound"): "not_verified", ("verified", "bound"): None,
    ("verified", "locked"): "locked", ("verified", "closed"): "session_closed",
    ("any", "unbound"): None, ("any", "bound"): None, ("any", "locked"): None,
    ("any", "closed"): "session_closed",
    ("worker", "unbound"): None, ("worker", "bound"): None, ("worker", "locked"): None,
    ("worker", "closed"): "session_closed",
}  # fmt: skip


@pytest.mark.parametrize(("scope", "state"), list(EXPECTED))
async def test_scope_matrix(gateway: Gateway, scope: Scope, state: str) -> None:
    s = await _state(gateway, state)
    result = await run_tool(gateway, scope, s.token, {}, REG)
    expected = EXPECTED[(scope, state)]
    if expected is None:
        assert result.ok and result.data == Out(echo="hi")
    else:
        assert not result.ok and result.error is not None and result.error.code == expected


@pytest.mark.parametrize("token", [None, "", "unknown-token"])
async def test_no_valid_token_is_forbidden(gateway: Gateway, token: str | None) -> None:
    result = await run_tool(gateway, "any", token, {}, REG)
    assert result.error is not None and result.error.code == "forbidden"


async def test_validation_errors_never_echo_values(gateway: Gateway) -> None:
    s = await _state(gateway, "bound")
    result = await run_tool(gateway, "any", s.token, {"customer_id": 9003, "word": "x"}, REG)
    assert result.error is not None and result.error.code == "invalid_input"
    assert "9003" not in result.error.message and "customer_id" in result.error.message


async def test_handler_denials_and_bank_errors(gateway: Gateway) -> None:
    s = await _state(gateway, "bound")
    denied = await run_tool(gateway, "any", s.token, {"word": "deny"}, REG)
    assert denied.error is not None and denied.error.code == "not_found"
    down = await run_tool(gateway, "any", s.token, {"word": "bank"}, REG)
    assert down.error is not None and down.error.code == "upstream_error"


async def test_unknown_tool(gateway: Gateway) -> None:
    s = await _state(gateway, "bound")
    result = await run_tool(gateway, "drop_tables", s.token, {}, REG)
    assert result.error is not None and result.error.code == "invalid_input"


async def test_session_identifiers_never_appear_in_output(gateway: Gateway) -> None:
    s = await _state(gateway, "bound")
    out = json.dumps((await run_tool(gateway, "verified", s.token, {}, REG)).model_dump(mode="json"))
    assert s.token not in out and s.call_id not in out and str(FRANK.customer_id) not in out
