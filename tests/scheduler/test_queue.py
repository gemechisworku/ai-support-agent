"""T-SCHED-01 · SCHED-R2: the de-duplicating `JobQueue` and its status transitions."""

import pytest

from supportagent.contracts.scheduler import CallJob, JobStatus
from supportagent.scheduler.queue import JobQueue
from tests.scheduler.conftest import NOW


def job(n: int = 1, *, customer: int = 1, loan: int = 11, campaign: str = "missed_payment") -> CallJob:
    return CallJob(
        job_id=f"job-{n}",
        call_id=f"00000000-0000-4000-8000-{n:012d}",
        campaign=campaign,  # type: ignore[arg-type]
        customer_id=customer,
        loan_id=loan,
        not_before=NOW,
        attempt_no=1,
        status="queued",
        reasons=[],
    )


def test_a_job_is_added_once_per_customer_loan_and_campaign() -> None:
    q = JobQueue()
    assert q.add(job(1)) is True
    assert q.add(job(2)) is False  # same key, still open
    assert [j.job_id for j in q.jobs()] == ["job-1"]


def test_the_key_is_customer_loan_and_campaign() -> None:
    q = JobQueue()
    assert q.add(job(1))
    assert q.add(job(2, loan=12))
    assert q.add(job(3, customer=2))
    assert q.add(job(4, campaign="callback"))
    assert len(q.jobs()) == 4


@pytest.mark.parametrize(
    ("status", "path"),
    [
        ("queued", []),
        ("dialing", ["dialing"]),
        ("in_call", ["dialing", "in_call"]),
        ("retry_scheduled", ["dialing", "retry_scheduled"]),
    ],
)
def test_an_open_job_blocks_a_duplicate(status: JobStatus, path: list[JobStatus]) -> None:
    q = JobQueue()
    q.add(job(1))
    for step in path:
        q.transition("job-1", step)
    assert q.get("job-1").status == status
    assert q.is_open(1, 11, "missed_payment") is True
    assert q.add(job(2)) is False


@pytest.mark.parametrize("final", ["completed", "failed", "cancelled_by_gate"])
def test_a_finished_job_no_longer_blocks_a_new_one(final: JobStatus) -> None:
    q = JobQueue()
    q.add(job(1))
    q.transition("job-1", "dialing")
    q.transition("job-1", final)
    assert q.is_open(1, 11, "missed_payment") is False
    assert q.add(job(2)) is True
    assert [j.job_id for j in q.open_jobs()] == ["job-2"]


def test_the_happy_path_of_status_transitions() -> None:
    q = JobQueue()
    q.add(job(1))
    for status in ("dialing", "in_call", "completed"):
        assert q.transition("job-1", status).status == status  # type: ignore[arg-type]
    assert q.get("job-1").status == "completed"


def test_a_retry_goes_back_to_dialing() -> None:
    q = JobQueue()
    q.add(job(1))
    q.transition("job-1", "dialing")
    q.transition("job-1", "retry_scheduled")
    assert q.transition("job-1", "dialing").status == "dialing"


def test_an_invalid_transition_is_refused() -> None:
    q = JobQueue()
    q.add(job(1))
    with pytest.raises(ValueError, match="queued -> completed"):
        q.transition("job-1", "completed")
    q.transition("job-1", "cancelled_by_gate")
    with pytest.raises(ValueError, match="cancelled_by_gate -> dialing"):
        q.transition("job-1", "dialing")


def test_a_gate_cancellation_records_its_reasons() -> None:
    q = JobQueue()
    q.add(job(1))
    done = q.transition("job-1", "cancelled_by_gate", reasons=["dnc", "outside_window"])
    assert done.reasons == ["dnc", "outside_window"]


def test_an_unknown_job_is_a_key_error() -> None:
    with pytest.raises(KeyError):
        JobQueue().transition("nope", "dialing")


def test_jobs_come_back_in_insertion_order() -> None:
    q = JobQueue()
    for n in (3, 1, 2):
        q.add(job(n, loan=10 + n))
    assert [j.job_id for j in q.jobs()] == ["job-3", "job-1", "job-2"]
