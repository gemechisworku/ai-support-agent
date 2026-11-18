"""Campaign candidate selection and the gated run (SCHED-R1, SCHED-R2).

    runner = CampaignRunner(GatewayServiceClient(...), rules, config, JobQueue())
    report = await runner.run_campaign("missed_payment")                 # dry run: nothing queued or dialled
    report = await runner.run_campaign("missed_payment", dry_run=False)  # queue the allowed jobs

Candidates come from the gateway's service scope (never the bank). `select_candidates` keeps those inside the
campaign's selector from `campaigns.yaml`; every one then goes through the compliance gate (`gate.admit`), and
the report lists the queued jobs and each exclusion with all its reason codes. This module never dials: the
`Dialer` (T-SCHED-02) takes jobs from the queue. Dates are compared with `clock.today()` (UTC).
"""

import datetime as dt
from collections.abc import Callable
from typing import Protocol

from supportagent.common import clock
from supportagent.common.logging import get_logger
from supportagent.contracts.compliance import ContactRules
from supportagent.contracts.gateway import Candidate
from supportagent.contracts.scheduler import CallJob, CampaignName, Exclusion, RunReport
from supportagent.scheduler.config import CampaignsConfig
from supportagent.scheduler.gate import admit, new_uuid
from supportagent.scheduler.queue import JobQueue

log = get_logger(__name__)


class CandidateSource(Protocol):
    async def candidates(self, campaign: CampaignName) -> list[Candidate]: ...


def _in_range(value: int, bounds: tuple[int, int] | None) -> bool:
    return bounds is not None and bounds[0] <= value <= bounds[1]


def select_candidates(
    campaign: CampaignName, candidates: list[Candidate], config: CampaignsConfig, today: dt.date
) -> list[Candidate]:
    """The candidates inside `campaign`'s selector, one per customer and loan, ordered by customer then loan.

    - `pre_due`: not past due, with the next instalment due 0..N days from `today`.
    - `missed_payment`: days past due within the configured range.
    - `callback`: due callback tasks merged with broken promises. A loan that appears as both is one
      candidate, and the one carrying the callback task wins.
    The gateway already filters by campaign; filtering here too keeps a wider answer (for example 45 days past
    due) out of the call list.
    """
    selector = config.campaigns[campaign].selector
    chosen: dict[tuple[int, int], Candidate] = {}
    for c in candidates:
        if str(c.campaign) != campaign:
            continue
        if campaign == "pre_due":
            if (
                c.due_date is None
                or c.days_past_due != 0
                or not _in_range((c.due_date - today).days, selector.due_in_days)
            ):
                continue
        elif campaign == "missed_payment" and not _in_range(c.days_past_due, selector.dpd):
            continue
        key = (c.customer_id, c.loan_id)
        held = chosen.get(key)
        if held is None or (held.task_id is None and c.task_id is not None):
            chosen[key] = c
    return [chosen[k] for k in sorted(chosen)]


class CampaignRunner:
    def __init__(
        self,
        source: CandidateSource,
        rules: ContactRules,
        config: CampaignsConfig,
        queue: JobQueue,
        *,
        new_id: Callable[[], str] = new_uuid,
    ) -> None:
        self._source = source
        self._rules = rules
        self._config = config
        self._queue = queue
        self._new_id = new_id

    async def run_campaign(
        self, name: CampaignName, dry_run: bool = True, *, now: dt.datetime | None = None
    ) -> RunReport:
        """Select, gate and (unless `dry_run`) queue. A dry run queues nothing."""
        if not self._config.campaigns[name].enabled:
            raise ValueError(f"campaign {name} is disabled in campaigns.yaml")
        now = now or clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        returned = await self._source.candidates(name)
        selected = select_candidates(name, returned, self._config, now.astimezone(dt.UTC).date())

        queued: list[CallJob] = []
        exclusions: list[Exclusion] = []
        already_open = 0
        for candidate in selected:
            verdict = admit(candidate, now, self._rules, new_id=self._new_id)
            if isinstance(verdict, Exclusion):
                exclusions.append(verdict)
            elif self._queue.is_open(verdict.customer_id, verdict.loan_id, verdict.campaign):
                already_open += 1
            else:
                if not dry_run:
                    self._queue.add(verdict)
                queued.append(verdict)

        report = RunReport(
            campaign=name,
            dry_run=dry_run,
            run_at=now,
            campaigns_version=self._config.version_tag,
            rules_version=self._rules.version_tag,
            selected=len(selected),
            not_selected=len(returned) - len(selected),
            queued=queued,
            exclusions=exclusions,
            already_open=already_open,
        )
        log.info(
            "campaign run",
            extra={
                "campaign": name,
                "dry_run": dry_run,
                "campaigns_version": report.campaigns_version,
                "rules_version": report.rules_version,
                "selected": report.selected,
                "not_selected": report.not_selected,
                "queued": len(queued),
                "excluded": len(exclusions),
                "already_open": already_open,
            },
        )
        for ex in exclusions:  # ids and reason codes only: no phone, name or other contact data
            log.info(
                "candidate excluded",
                extra={"campaign": name, "loan_id": ex.loan_id, "reasons": ex.reasons},
            )
        return report
