"""The in-process job queue (SCHED-R2): de-duplicated by customer, loan and campaign; checked transitions.

A job is *open* until it reaches `completed`, `failed` or `cancelled_by_gate`; while one is open, the same
customer, loan and campaign cannot be queued again, so re-running a campaign never duplicates a call.
The queue is in-process by design (plan: the bank's attempt log, written through the gateway, is what the
frequency cap counts, so a restart cannot cause extra calls to a capped customer).
"""

from supportagent.contracts.scheduler import CallJob, CampaignName, JobStatus

TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    "queued": frozenset({"dialing", "cancelled_by_gate", "failed"}),
    "dialing": frozenset({"in_call", "retry_scheduled", "completed", "cancelled_by_gate", "failed"}),
    "in_call": frozenset({"completed", "retry_scheduled", "failed"}),
    "retry_scheduled": frozenset({"dialing", "cancelled_by_gate", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled_by_gate": frozenset(),
}
TERMINAL: frozenset[JobStatus] = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)

type JobKey = tuple[int, int, CampaignName]


def key_of(job: CallJob) -> JobKey:
    return (job.customer_id, job.loan_id, job.campaign)


class JobQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, CallJob] = {}  # insertion-ordered

    def is_open(self, customer_id: int, loan_id: int, campaign: CampaignName) -> bool:
        return any(
            key_of(j) == (customer_id, loan_id, campaign) and j.status not in TERMINAL
            for j in self._jobs.values()
        )

    def add(self, job: CallJob) -> bool:
        """Queue `job` unless one is open for its customer, loan and campaign. True if it was added."""
        if job.job_id in self._jobs:
            raise ValueError(f"job {job.job_id} is already in the queue")
        if self.is_open(*key_of(job)):
            return False
        self._jobs[job.job_id] = job
        return True

    def get(self, job_id: str) -> CallJob:
        return self._jobs[job_id]

    def transition(self, job_id: str, status: JobStatus, *, reasons: list[str] | None = None) -> CallJob:
        job = self._jobs[job_id]
        if status not in TRANSITIONS[job.status]:
            raise ValueError(f"job {job_id}: cannot go {job.status} -> {status}")
        update: dict[str, object] = {"status": status}
        if reasons is not None:
            update["reasons"] = reasons
        self._jobs[job_id] = job = job.model_copy(update=update)
        return job

    def jobs(self) -> list[CallJob]:
        return list(self._jobs.values())

    def open_jobs(self) -> list[CallJob]:
        return [j for j in self._jobs.values() if j.status not in TERMINAL]
