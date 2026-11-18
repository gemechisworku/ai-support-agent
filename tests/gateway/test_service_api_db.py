"""T-GW-10 (integration): the service API over the real bank API and the seeded `bank_test` database.

Frank (11 days late) is a missed_payment candidate, Olivia (due in 2 days) a pre_due one, and Sam is
still listed with `cease_contact=true`: filtering on contact rules is the scheduler's job.
"""

import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DbSession

from supportagent.bank import models
from supportagent.bank.api import create_app as create_bank_app
from supportagent.bank.api.deps import get_session
from supportagent.common.clock import FrozenClock
from supportagent.common.settings import Settings
from supportagent.contracts.gateway import Candidate, ContactPrefs
from supportagent.gateway.app import create_app
from supportagent.gateway.bank_client import HttpBankClient
from supportagent.gateway.tools import Gateway
from tests.bank.conftest import seeded_bank  # noqa: F401 (fixture)
from tests.conftest import TEST_ENV
from tests.gateway.conftest import CONFIG
from tests.gateway.fakes import FRANK, OLIVIA, SAM

pytestmark = pytest.mark.integration
SERVICE = {"Authorization": f"Bearer {CONFIG.service_token}"}


@pytest.fixture
def client(
    seeded_bank: Settings,  # noqa: F811
    bank_db: DbSession,
    settings: Settings,
    frozen_clock: FrozenClock,
) -> Iterator[TestClient]:
    bank_app = create_bank_app()

    def session_override() -> Iterator[DbSession]:
        yield bank_db
        bank_db.flush()

    bank_app.dependency_overrides[get_session] = session_override
    bank = HttpBankClient(
        "http://bank",
        TEST_ENV["GATEWAY_SERVICE_TOKEN"],
        transport=httpx.ASGITransport(app=bank_app),
        timeout=30,
    )
    yield TestClient(create_app(Gateway(bank=bank, config=CONFIG)))


def _candidates(client: TestClient, campaign: str) -> dict[int, Candidate]:
    r = client.get(f"/service/candidates?campaign={campaign}", headers=SERVICE)
    assert r.status_code == 200, r.text
    return {c.customer_id: c for c in map(Candidate.model_validate, r.json())}


def test_frank_is_a_missed_payment_candidate(client: TestClient) -> None:
    frank = _candidates(client, "missed_payment")[FRANK.customer_id]
    assert frank.loan_id == FRANK.loan_id and frank.days_past_due == FRANK.days_past_due
    assert frank.phone == FRANK.phone and frank.timezone and frank.amount_due is not None


def test_olivia_is_a_pre_due_candidate(client: TestClient) -> None:
    olivia = _candidates(client, "pre_due")[OLIVIA.customer_id]
    assert olivia.due_date is not None and olivia.amount_due is not None
    assert olivia.campaign == "pre_due"


def test_sam_is_listed_with_cease_contact(client: TestClient, bank_db: DbSession) -> None:
    prefs = bank_db.get(models.ContactPreference, SAM.customer_id)
    assert prefs is not None
    prefs.cease_contact = True
    bank_db.flush()
    sam = _candidates(client, "missed_payment")[SAM.customer_id]
    assert sam.contact.cease_contact is True


def test_contact_preferences_match_the_candidate_payload(client: TestClient, bank_db: DbSession) -> None:
    prefs = bank_db.get(models.ContactPreference, SAM.customer_id)
    assert prefs is not None
    prefs.cease_contact = True
    bank_db.flush()
    sam = _candidates(client, "missed_payment")[SAM.customer_id]
    r = client.get(f"/service/contact-preferences/{SAM.customer_id}", headers=SERVICE)
    assert r.status_code == 200 and ContactPrefs.model_validate(r.json()) == sam.contact


def test_the_callback_list_is_valid_and_carries_the_gate_facts(client: TestClient) -> None:
    for cand in _candidates(client, "callback").values():
        assert cand.campaign == "callback" and cand.timezone and cand.phone


def test_an_attempt_creates_an_interaction_row(client: TestClient) -> None:
    call_id = str(uuid.uuid4())
    body = {
        "call_id": call_id,
        "customer_id": FRANK.customer_id,
        "loan_id": FRANK.loan_id,
        "campaign": "missed_payment",
        "contact_result": "voicemail",
        "started_at": "2026-11-09T14:00:00+00:00",
    }
    r = client.post("/service/attempts", json=body, headers=SERVICE)
    assert r.status_code == 200 and r.json() == {"interaction_id": call_id}
    frank = _candidates(client, "missed_payment")[FRANK.customer_id]
    assert frank.attempts_last_7d >= 1  # the new outbound attempt is counted by the bank
