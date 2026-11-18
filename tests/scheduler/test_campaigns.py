"""T-SCHED-01 · SCHED-R1, SCHED-R2: campaign selection, the gated run and its report."""

import datetime as dt
import json
import logging

import pytest

from supportagent.contracts.compliance import ContactRules
from supportagent.contracts.gateway import Candidate
from supportagent.scheduler.campaigns import CampaignRunner, select_candidates
from supportagent.scheduler.config import CampaignsConfig
from supportagent.scheduler.queue import JobQueue
from tests.scheduler.conftest import NOW, TODAY, FakeGateway, candidate, dawit, frank, hana, olivia, sam

pytestmark = pytest.mark.usefixtures("frozen")


def runner(
    gateway: FakeGateway, rules: ContactRules, config: CampaignsConfig, queue: JobQueue | None = None
) -> CampaignRunner:
    return CampaignRunner(gateway, rules, config, queue or JobQueue())


def keys(cands: list[Candidate]) -> list[tuple[int, int]]:
    return [(c.customer_id, c.loan_id) for c in cands]


# --------------------------------------------------------------------------- selection (SCHED-R1)


def test_pre_due_selects_instalments_due_in_0_to_3_days(config: CampaignsConfig) -> None:
    cands = [
        candidate(1, 1, "pre_due", due_in_days=0),
        candidate(2, 2, "pre_due", due_in_days=3),
        candidate(3, 3, "pre_due", due_in_days=4),  # too early
        candidate(4, 4, "pre_due", due_in_days=1, dpd=0),
        candidate(5, 5, "pre_due", dpd=2),  # already past due: that is missed_payment
    ]
    assert keys(select_candidates("pre_due", cands, config, TODAY)) == [(1, 1), (2, 2), (4, 4)]


def test_pre_due_ignores_a_candidate_with_no_due_date(config: CampaignsConfig) -> None:
    no_date = candidate(1, 1, "pre_due", due_in_days=1).model_copy(update={"due_date": None})
    assert select_candidates("pre_due", [no_date], config, TODAY) == []


def test_missed_payment_selects_1_to_30_dpd_and_drops_the_rest(config: CampaignsConfig) -> None:
    cands = [candidate(n, n, dpd=d) for n, d in enumerate((0, 1, 15, 30, 31, 90), start=1)]
    assert keys(select_candidates("missed_payment", cands, config, TODAY)) == [(2, 2), (3, 3), (4, 4)]


def test_a_candidate_for_another_campaign_is_dropped(config: CampaignsConfig) -> None:
    assert select_candidates("missed_payment", [olivia()], config, TODAY) == []


def test_callback_merges_due_callbacks_with_broken_promises(config: CampaignsConfig) -> None:
    task = candidate(1, 11, "callback", dpd=5, task_id=7)
    broken = candidate(2, 21, "callback", dpd=9)  # a broken promise: no task id
    both_broken = candidate(1, 11, "callback", dpd=5)  # the same loan again, from the promise
    merged = select_candidates("callback", [both_broken, task, broken], config, TODAY)
    assert keys(merged) == [(1, 11), (2, 21)]
    assert merged[0].task_id == 7  # the callback task wins over the bare promise


@pytest.mark.asyncio
async def test_the_report_for_missed_payment_lists_frank_and_hana_and_excludes_sam_and_dawit(
    rules: ContactRules, config: CampaignsConfig
) -> None:
    gw = FakeGateway(missed_payment=[frank(), hana(), sam(), dawit(), candidate(9, 91, dpd=45)])
    report = await runner(gw, rules, config).run_campaign("missed_payment")
    assert [(j.customer_id, j.loan_id) for j in report.queued] == [(1, 11), (2, 21)]
    assert {(e.customer_id): e.reasons for e in report.exclusions} == {
        4: ["cease_contact"],
        3: ["dispute_open"],
    }
    assert report.not_selected == 1  # 45 days past due is outside 1-30
    assert gw.calls == ["missed_payment"]


@pytest.mark.asyncio
async def test_pre_due_lists_olivia(rules: ContactRules, config: CampaignsConfig) -> None:
    report = await runner(FakeGateway(pre_due=[olivia()]), rules, config).run_campaign("pre_due")
    assert [(j.customer_id, j.campaign) for j in report.queued] == [(5, "pre_due")]


# --------------------------------------------------------------------------- the run (SCHED-R2)


@pytest.mark.asyncio
async def test_a_dry_run_reports_jobs_without_queueing_or_dialling(
    rules: ContactRules, config: CampaignsConfig
) -> None:
    queue = JobQueue()
    report = await runner(FakeGateway(missed_payment=[frank()]), rules, config, queue).run_campaign(
        "missed_payment"
    )
    assert report.dry_run is True and len(report.queued) == 1
    assert queue.jobs() == []  # nothing was queued, so nothing can be dialled


@pytest.mark.asyncio
async def test_a_real_run_queues_the_jobs(rules: ContactRules, config: CampaignsConfig) -> None:
    queue = JobQueue()
    report = await runner(FakeGateway(missed_payment=[frank(), hana()]), rules, config, queue).run_campaign(
        "missed_payment", dry_run=False
    )
    assert report.dry_run is False
    assert [j.job_id for j in queue.jobs()] == [j.job_id for j in report.queued]
    assert all(j.status == "queued" and j.not_before == NOW for j in queue.jobs())


@pytest.mark.asyncio
async def test_rerunning_does_not_duplicate_an_open_job(rules: ContactRules, config: CampaignsConfig) -> None:
    queue = JobQueue()
    r = runner(FakeGateway(missed_payment=[frank(), hana()]), rules, config, queue)
    first = await r.run_campaign("missed_payment", dry_run=False)
    second = await r.run_campaign("missed_payment", dry_run=False)
    assert len(first.queued) == 2
    assert second.queued == [] and second.already_open == 2
    assert len(queue.jobs()) == 2


@pytest.mark.asyncio
async def test_a_dry_run_after_a_real_run_reports_the_open_jobs(
    rules: ContactRules, config: CampaignsConfig
) -> None:
    queue = JobQueue()
    r = runner(FakeGateway(missed_payment=[frank()]), rules, config, queue)
    await r.run_campaign("missed_payment", dry_run=False)
    report = await r.run_campaign("missed_payment")
    assert report.queued == [] and report.already_open == 1


@pytest.mark.asyncio
async def test_a_finished_job_is_queued_again(rules: ContactRules, config: CampaignsConfig) -> None:
    queue = JobQueue()
    r = runner(FakeGateway(missed_payment=[frank()]), rules, config, queue)
    first = await r.run_campaign("missed_payment", dry_run=False)
    job_id = first.queued[0].job_id
    queue.transition(job_id, "dialing")
    queue.transition(job_id, "completed")
    second = await r.run_campaign("missed_payment", dry_run=False)
    assert len(second.queued) == 1 and len(queue.jobs()) == 2


@pytest.mark.asyncio
async def test_an_excluded_candidate_is_never_queued_even_for_a_real_run(
    rules: ContactRules, config: CampaignsConfig
) -> None:
    queue = JobQueue()
    report = await runner(FakeGateway(missed_payment=[sam()]), rules, config, queue).run_campaign(
        "missed_payment", dry_run=False
    )
    assert report.queued == [] and queue.jobs() == []
    assert [(e.customer_id, e.reasons) for e in report.exclusions] == [(4, ["cease_contact"])]


@pytest.mark.asyncio
async def test_the_report_carries_both_config_versions(rules: ContactRules, config: CampaignsConfig) -> None:
    report = await runner(FakeGateway(), rules, config).run_campaign("callback")
    assert report.campaigns_version == config.version_tag == "campaigns@1.0"
    assert report.rules_version == rules.version_tag
    assert report.run_at == NOW and report.campaign == "callback"


@pytest.mark.asyncio
async def test_the_campaigns_version_is_logged_with_each_run(
    rules: ContactRules, config: CampaignsConfig, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="supportagent.scheduler.campaigns"):
        await runner(FakeGateway(missed_payment=[frank(), sam()]), rules, config).run_campaign(
            "missed_payment"
        )
    rec = next(r for r in caplog.records if r.getMessage() == "campaign run")
    assert rec.campaigns_version == "campaigns@1.0"  # type: ignore[attr-defined]
    assert rec.rules_version == rules.version_tag  # type: ignore[attr-defined]
    assert (rec.queued, rec.excluded) == (1, 1)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_log_carries_no_phone_numbers(
    rules: ContactRules, config: CampaignsConfig, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="supportagent.scheduler"):
        await runner(FakeGateway(missed_payment=[frank(), sam()]), rules, config).run_campaign(
            "missed_payment"
        )
    text = json.dumps([r.__dict__.get("reasons") for r in caplog.records], default=str) + " ".join(
        r.getMessage() for r in caplog.records
    )
    assert "+1555" not in text


@pytest.mark.asyncio
async def test_a_disabled_campaign_does_not_run(rules: ContactRules, config: CampaignsConfig) -> None:
    disabled = config.model_copy(
        update={
            "campaigns": {
                **config.campaigns,
                "callback": config.campaigns["callback"].model_copy(update={"enabled": False}),
            }
        }
    )
    gw = FakeGateway(callback=[candidate(1, 1, "callback", dpd=3)])
    with pytest.raises(ValueError, match="callback is disabled"):
        await runner(gw, rules, disabled).run_campaign("callback")
    assert gw.calls == []


@pytest.mark.asyncio
async def test_a_naive_run_time_is_rejected(rules: ContactRules, config: CampaignsConfig) -> None:
    r = runner(FakeGateway(), rules, config)
    with pytest.raises(ValueError, match="timezone-aware"):
        await r.run_campaign("callback", now=dt.datetime(2026, 11, 9, 15, 0))


@pytest.mark.asyncio
async def test_the_queued_campaign_is_a_plain_string_not_the_gateways_enum(
    rules: ContactRules, config: CampaignsConfig
) -> None:
    """The gateway's `campaign` is a `Campaign` StrEnum (the fixtures use it); jobs carry the plain name."""
    cand = frank()
    assert type(cand.campaign) is not str
    report = await runner(FakeGateway(missed_payment=[cand]), rules, config).run_campaign("missed_payment")
    assert [(j.customer_id, j.campaign) for j in report.queued] == [(1, "missed_payment")]
    assert type(report.queued[0].campaign) is str
