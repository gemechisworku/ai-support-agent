"""T-EVAL-01 · EVAL-R1: `FakeGateway` behaves like the real gateway (scope, lock, masking, promise window,
contact flags, audit rows). The same cases run against three targets:

- `fake`: the in-memory `FakeGateway` (unit);
- `gateway-fakebank`: the real tool pipeline over the persona seed in `FakeBank` (unit);
- `gateway-db`: the real pipeline over the real bank API and the seeded `bank_test` database
  (`-m integration`, `make test-int`).

A case is one async function over a `Target`; it asserts literal expectations, so a target that disagrees
fails. The agent may only rely on the fake because this suite keeps it honest.
"""

import datetime as dt
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from supportagent.bank.api import create_app as create_bank_app
from supportagent.bank.api.deps import get_session
from supportagent.common.clock import FrozenClock
from supportagent.common.settings import Settings, get_settings
from supportagent.contracts.gateway import EscalationReason, SessionCreate
from supportagent.contracts.outcome import CallOutcome, Campaign, ContactResult, Direction, Versions
from supportagent.evals.fakes import args_hash
from supportagent.gateway.bank_client import HttpBankClient
from supportagent.gateway.tools import Gateway
from tests.bank.conftest import seeded_bank  # noqa: F401 (fixture)
from tests.conftest import TEST_ENV
from tests.evals.targets import (
    SALT,
    AuditRows,
    InProcessPort,
    Target,
    gateway_config,
    open_fake,
    open_gateway_fakebank,
    session_request,
)
from tests.gateway.fakes import AS_OF, FRANK, FakeBank

OpenTarget = Callable[[SessionCreate], Awaitable[Target]]
FRANK_STATUS = FakeBank().status_of(FRANK.loan_id)
FRANK_CLAIM = {"full_name": FRANK.name, "dob": FRANK.dob, "loan_last4": FRANK.loan_last4}
LONG_DIGITS = re.compile(r"\d{5,}")


@pytest.fixture
async def open_db(
    seeded_bank: Settings,  # noqa: F811
    bank_db: Session,
    settings: Settings,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[OpenTarget]:
    """Real gateway pipeline → real bank API (in-process ASGI) → seeded `bank_test`."""
    monkeypatch.setenv("BANK_AS_OF", AS_OF.isoformat())
    get_settings.cache_clear()
    bank_app = create_bank_app()

    def session_override():  # type: ignore[no-untyped-def]
        yield bank_db
        bank_db.flush()

    bank_app.dependency_overrides[get_session] = session_override
    config = gateway_config(TEST_ENV["GATEWAY_SERVICE_TOKEN"])  # the token the bank API checks
    bank = HttpBankClient(
        "http://bank", config.service_token, transport=httpx.ASGITransport(app=bank_app), timeout=30
    )
    gw = Gateway(bank=bank, config=config)

    async def open_(req: SessionCreate) -> Target:
        session = await gw.store.create(req, gw.bank)

        async def audit() -> AuditRows:
            rows = bank_db.execute(
                text(
                    "select tool, decision, customer_id, args_hash "
                    "from audit_log where call_id = :c order by id"
                ),
                {"c": req.call_id},
            ).all()
            return [(r.tool, r.decision, r.customer_id, r.args_hash) for r in rows]

        return Target(InProcessPort(gw, session.token), req.call_id, audit)

    yield open_
    get_settings.cache_clear()


@pytest.fixture(
    params=["fake", "gateway-fakebank", pytest.param("gateway-db", marks=pytest.mark.integration)]
)
def open_target(request: pytest.FixtureRequest, frozen_clock: FrozenClock) -> OpenTarget:
    if request.param == "fake":
        return open_fake
    if request.param == "gateway-fakebank":
        return open_gateway_fakebank
    return request.getfixturevalue("open_db")  # type: ignore[no-any-return]


# -------------------- helpers


def error_code(result: object) -> str | None:
    error = getattr(result, "error", None)
    return None if error is None else str(error.code)


async def outbound(open_target: OpenTarget, **overrides: object) -> Target:
    return await open_target(session_request(loan_id=FRANK.loan_id, **overrides))


async def verified(open_target: OpenTarget) -> Target:
    t = await outbound(open_target)
    result = await t.port.verify_identity(**FRANK_CLAIM)
    assert result.ok and result.data and result.data.verified
    return t


def decisions(rows: AuditRows) -> list[tuple[str, str, int | None]]:
    return [(tool, decision, customer) for tool, decision, customer, _ in rows]


# -------------------- cases: scope


async def test_data_tools_refuse_an_unverified_call(open_target: OpenTarget) -> None:
    t = await outbound(open_target)
    refused = [
        await t.port.get_loan_status(),
        await t.port.get_recent_transactions(),
        await t.port.get_interaction_history(),
        await t.port.record_promise_to_pay(AS_OF + dt.timedelta(days=3), Decimal("10.00")),
        await t.port.set_contact_flag("dispute"),
    ]
    assert [error_code(r) for r in refused] == ["not_verified"] * 5
    assert all(r.data is None for r in refused)
    assert decisions(await t.audit()) == [
        (tool, "deny", None)
        for tool in (
            "get_loan_status",
            "get_recent_transactions",
            "get_interaction_history",
            "record_promise_to_pay",
            "set_contact_flag",
        )
    ]


async def test_any_scope_tools_work_before_verification(open_target: OpenTarget) -> None:
    t = await outbound(open_target)
    escalation = await t.port.escalate(EscalationReason.human_requested, "caller asked for a person")
    assert escalation.ok and escalation.data is not None
    assert escalation.data.human_available is True and escalation.data.transfer_extension == "200"
    assert escalation.data.task_id is not None  # an outbound call knows whose loan it is about
    callback = await t.port.request_callback(dt.datetime(2026, 11, 11, 15, tzinfo=dt.UTC), "busy now")
    assert callback.ok and callback.data is not None and callback.data.type == "callback"
    assert decisions(await t.audit()) == [("escalate", "allow", None), ("request_callback", "allow", None)]


async def test_an_unknown_loan_ending_is_not_found_not_someone_elses_loan(open_target: OpenTarget) -> None:
    t = await verified(open_target)
    result = await t.port.get_loan_status("0000")
    assert error_code(result) == "not_found"


# -------------------- cases: identity


async def test_verification_binds_the_customer_and_audit_shows_it(open_target: OpenTarget) -> None:
    t = await outbound(open_target)
    result = await t.port.verify_identity(**FRANK_CLAIM)
    assert result.data is not None
    assert (result.data.verified, result.data.attempts_remaining, result.data.locked) == (True, 2, False)
    assert result.data.first_name == FRANK.name.split()[0]
    status = await t.port.get_loan_status()
    assert status.ok
    rows = await t.audit()
    assert decisions(rows) == [
        ("verify_identity", "allow", FRANK.customer_id),
        ("get_loan_status", "allow", FRANK.customer_id),
    ]
    expected = {"full_name": FRANK.name, "dob": FRANK.dob.isoformat(), "loan_last4": FRANK.loan_last4}
    assert rows[0][3] == args_hash(SALT, expected)  # same salted hash of the same arguments
    assert rows[1][3] == args_hash(SALT, {})


async def test_names_may_be_off_by_one_edit_but_not_more(open_target: OpenTarget) -> None:
    close = await outbound(open_target)
    first = FRANK.name.split()[0]
    typo = f"{first[:-1]} {FRANK.name.split()[-1]}"  # one letter short: speech-to-text spelling
    assert (await close.port.verify_identity(typo, FRANK.dob, FRANK.loan_last4)).data.verified is True  # type: ignore[union-attr]
    wrong = await outbound(open_target)
    result = await wrong.port.verify_identity("Franklin Smithers", FRANK.dob, FRANK.loan_last4)
    assert result.data is not None and (result.data.verified, result.data.attempts_remaining) == (False, 1)


async def test_two_failures_lock_the_call_for_good(open_target: OpenTarget) -> None:
    t = await outbound(open_target)
    wrong_dob = FRANK.dob + dt.timedelta(days=1)
    one = await t.port.verify_identity(FRANK.name, wrong_dob, FRANK.loan_last4)
    two = await t.port.verify_identity(FRANK.name, wrong_dob, FRANK.loan_last4)
    assert one.data is not None and (one.data.verified, one.data.locked, one.data.attempts_remaining) == (
        False,
        False,
        1,
    )
    assert two.data is not None and (two.data.verified, two.data.locked, two.data.attempts_remaining) == (
        False,
        True,
        0,
    )

    right_but_late = await t.port.verify_identity(**FRANK_CLAIM)
    assert error_code(right_but_late) == "locked"
    assert error_code(await t.port.get_loan_status()) == "locked"
    assert (
        await t.port.escalate(EscalationReason.verification_failed, "locked out")
    ).ok  # any scope still works
    assert decisions(await t.audit()) == [
        ("verify_identity", "allow", None),
        ("verify_identity", "allow", None),
        ("verify_identity", "deny", None),
        ("get_loan_status", "deny", None),
        ("escalate", "allow", None),
    ]


async def test_an_inbound_caller_is_found_by_phone_or_by_dob_and_last4(open_target: OpenTarget) -> None:
    by_phone = await open_target(
        session_request(direction=Direction.inbound, campaign=Campaign.inbound, caller_phone=FRANK.phone)
    )
    lookup = await by_phone.port.lookup_by_phone()
    assert lookup.data is not None and (lookup.data.match, lookup.data.first_name) == (
        True,
        None,
    )  # no name inbound

    anonymous = await open_target(session_request(direction=Direction.inbound, campaign=Campaign.inbound))
    assert (await anonymous.port.lookup_by_phone()).data.match is False  # type: ignore[union-attr]
    result = await anonymous.port.verify_identity(**FRANK_CLAIM)
    assert result.data is not None and result.data.verified is True

    outbound_call = await outbound(open_target)
    first = await outbound_call.port.lookup_by_phone()
    assert first.data is not None and (first.data.match, first.data.first_name) == (
        True,
        FRANK.name.split()[0],
    )


# -------------------- cases: masking


async def test_outputs_carry_masked_numbers_and_the_banks_figures(open_target: OpenTarget) -> None:
    t = await verified(open_target)
    status = await t.port.get_loan_status()
    assert status.data is not None and len(status.data.loans) == 1
    loan = status.data.loans[0]
    assert loan.loan_masked == f"****{FRANK.loan_last4}"
    assert (
        loan.overdue_amount == FRANK_STATUS.overdue_amount
        and loan.days_past_due == FRANK_STATUS.days_past_due
    )
    assert loan.oldest_unpaid_due_date == FRANK_STATUS.oldest_unpaid_due_date
    assert loan.speakable.startswith(f"${FRANK_STATUS.overdue_amount:,.2f} overdue since")
    transactions = await t.port.get_recent_transactions()
    assert transactions.ok and transactions.data is not None and len(transactions.data.transactions) <= 5
    history = await t.port.get_interaction_history()
    assert history.ok
    for result in (status, transactions, history):
        assert not LONG_DIGITS.search(result.model_dump_json()), "5+ digit run in a tool output"


# -------------------- cases: promises


async def test_a_promise_must_fall_in_the_window_and_not_exceed_the_amount_due(
    open_target: OpenTarget,
) -> None:
    t = await verified(open_target)
    due = FRANK_STATUS.overdue_amount
    horizon = AS_OF + dt.timedelta(days=14)

    async def promise(days: int, amount: Decimal):  # type: ignore[no-untyped-def]
        result = await t.port.record_promise_to_pay(AS_OF + dt.timedelta(days=days), amount)
        assert result.ok and result.data is not None
        assert result.data.max_date == horizon and result.data.amount_due == due
        return result.data

    assert (await promise(-1, due)).reason == "date_out_of_window"
    assert (await promise(15, due)).reason == "date_out_of_window"
    assert (await promise(3, due + Decimal("0.01"))).reason == "amount_invalid"
    assert (await promise(3, Decimal("0.00"))).reason == "amount_invalid"
    accepted = await promise(3, due)
    assert (accepted.accepted, accepted.reason) == (True, "ok") and accepted.promise_id is not None
    partial = await promise(14, (due / 2).quantize(Decimal("0.01")))
    assert (partial.accepted, partial.reason) == (True, "ok")  # partial payments are allowed
    unknown = await t.port.record_promise_to_pay(AS_OF + dt.timedelta(days=3), due, loan_last4="0000")
    assert unknown.data is not None and (unknown.data.accepted, unknown.data.reason) == (
        False,
        "loan_not_found",
    )


# -------------------- cases: contact flags


async def test_contact_flags_need_verification_and_then_apply(open_target: OpenTarget) -> None:
    unverified = await outbound(open_target)
    assert error_code(await unverified.port.set_contact_flag("cease_contact")) == "not_verified"

    t = await verified(open_target)
    for flag in ("dispute", "cease_contact"):
        result = await t.port.set_contact_flag(flag, note="caller said so")  # type: ignore[arg-type]
        assert result.data is not None and (result.data.flag, result.data.applied) == (flag, True)
    window = await t.port.set_contact_flag("preferred_window", preferred_window="18:00-20:00")
    assert window.data is not None and window.data.applied is True


# -------------------- cases: callbacks, escalation, outcome


async def test_callbacks_must_be_in_the_next_30_days(open_target: OpenTarget) -> None:
    t = await outbound(open_target)
    now = dt.datetime(2026, 11, 9, 14, 0, tzinfo=dt.UTC)
    assert (await t.port.request_callback(now + dt.timedelta(days=2), "later")).ok
    assert (await t.port.request_callback(now + dt.timedelta(days=30), "edge")).ok
    assert error_code(await t.port.request_callback(now - dt.timedelta(hours=1), "past")) == "invalid_input"
    assert error_code(await t.port.request_callback(now + dt.timedelta(days=31), "far")) == "invalid_input"


async def test_the_outcome_is_written_once_and_closes_the_call(open_target: OpenTarget) -> None:
    t = await verified(open_target)

    def outcome(**overrides: object) -> CallOutcome:
        values: dict[str, object] = {
            "call_id": t.call_id,
            "direction": Direction.outbound,
            "campaign": Campaign.missed_payment,
            "customer_id": FRANK.customer_id,
            "loan_id": FRANK.loan_id,
            "contact_result": ContactResult.right_party,
            "verified": True,
            "versions": Versions(prompt="p", policy="q", model="m"),
        }
        values.update(overrides)
        return CallOutcome(**values)  # type: ignore[arg-type]

    assert (
        error_code(await t.port.record_call_outcome(outcome(customer_id=FRANK.customer_id + 1)))
        == "forbidden"
    )
    written = await t.port.record_call_outcome(outcome())
    assert written.ok and written.data is not None and written.data.interaction_id == t.call_id
    assert error_code(await t.port.get_loan_status()) == "session_closed"
    assert [d for _, d, _ in decisions(await t.audit())][-3:] == ["deny", "allow", "deny"]
