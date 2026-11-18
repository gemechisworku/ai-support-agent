"""T-AGENT-01 · AGENT-R1: parsing the call context from room metadata or SIP attributes."""

import json
import uuid

import pytest

from supportagent.agent.context import (
    SIP_PHONE_ATTRIBUTE,
    CallState,
    InvalidCallMetadata,
    parse_call_metadata,
)
from supportagent.contracts.call import CallMetadata
from supportagent.contracts.outcome import Campaign, Direction

CALL_ID = "fbde0dde-804f-4a79-ba80-2795d29b3079"


def test_outbound_room_metadata_json() -> None:
    raw = json.dumps(
        {
            "call_id": CALL_ID,
            "direction": "outbound",
            "campaign": "missed_payment",
            "customer_id": 9002,
            "loan_id": 17,
        }
    )
    meta = parse_call_metadata(raw)
    assert meta == CallMetadata(
        call_id=CALL_ID,
        direction=Direction.outbound,
        campaign=Campaign.missed_payment,
        customer_id=9002,
        loan_id=17,
    )
    assert meta.callback_task_id is None and meta.caller_phone is None


def test_callback_campaign_carries_the_task_id() -> None:
    raw = json.dumps(
        {"call_id": CALL_ID, "direction": "outbound", "campaign": "callback", "callback_task_id": 5}
    )
    assert parse_call_metadata(raw).callback_task_id == 5


def test_missing_call_id_generates_a_uuid4() -> None:
    meta = parse_call_metadata(json.dumps({"direction": "outbound", "campaign": "pre_due"}))
    assert uuid.UUID(meta.call_id).version == 4
    other = parse_call_metadata(json.dumps({"direction": "outbound", "campaign": "pre_due"}))
    assert other.call_id != meta.call_id


def test_inbound_sip_attributes() -> None:
    meta = parse_call_metadata(None, {SIP_PHONE_ATTRIBUTE: "+15550100302", "sip.callID": "abc"})
    assert (meta.direction, meta.campaign) == (Direction.inbound, Campaign.inbound)
    assert meta.caller_phone == "+15550100302"
    assert uuid.UUID(meta.call_id).version == 4
    assert meta.customer_id is None and meta.loan_id is None


def test_inbound_with_a_withheld_or_odd_number_has_no_caller_phone() -> None:
    meta = parse_call_metadata("", {SIP_PHONE_ATTRIBUTE: "anonymous"})
    assert meta.campaign == Campaign.inbound and meta.caller_phone is None


@pytest.mark.parametrize("room_metadata", [None, "", "   "])
def test_a_room_without_metadata_is_a_playground_call(room_metadata: str | None) -> None:
    meta = parse_call_metadata(room_metadata, {})
    assert meta.campaign == Campaign.playground
    assert meta.direction == Direction.inbound
    assert uuid.UUID(meta.call_id).version == 4


def test_room_metadata_wins_over_sip_attributes() -> None:
    raw = json.dumps({"call_id": CALL_ID, "direction": "outbound", "campaign": "pre_due"})
    meta = parse_call_metadata(raw, {SIP_PHONE_ATTRIBUTE: "+15550100302"})
    assert meta.direction == Direction.outbound and meta.caller_phone is None


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "[1, 2]",
        json.dumps({"direction": "sideways", "campaign": "pre_due"}),
        json.dumps({"direction": "outbound", "campaign": "pre_due", "call_id": "not-a-uuid"}),
        json.dumps({"direction": "outbound", "campaign": "pre_due", "surprise": 1}),
    ],
)
def test_invalid_metadata_is_rejected_without_echoing_it(raw: str) -> None:
    with pytest.raises(InvalidCallMetadata) as err:
        parse_call_metadata(raw)
    assert "not-a-uuid" not in str(err.value) and "sideways" not in str(err.value)


def test_call_state_never_shows_the_token_or_the_gateway() -> None:
    from pydantic import SecretStr

    state = CallState(
        metadata=parse_call_metadata(None),
        gateway=object(),  # type: ignore[arg-type]
        session_token=SecretStr("tok-secret-123"),
    )
    text = repr(state)
    assert "tok-secret-123" not in text and "gateway" not in text
    assert state.call_id == state.metadata.call_id
