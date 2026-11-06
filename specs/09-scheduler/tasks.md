# 09 · Outreach scheduler — Tasks

> Each task is one GitHub issue and one PR. The field bullets are parsed by `scripts/tasks.py`, so keep
> their exact format. Status is `todo` or `done`. Issue is `—` until `make issues-sync` fills it in.
> Ids are never reused. Order tasks by dependency.

## Tasks

### T-SCHED-01 · Campaign definitions and candidate queue
- **Status:** todo
- **Milestone:** M3
- **Issue:** #54
- **Depends on:** T-COMP-04, T-GW-10
- **Requirements:** SCHED-R1, SCHED-R2

Add the scheduler contracts, the `campaigns.yaml` config, the gateway service-scope client, the
campaign candidate sources, the compliance-gate adapter and the de-duplicating `JobQueue`. See
[plan.md](plan.md#data-model--contracts). `run_campaign(name, dry_run=True)` returns a `RunReport`
of queued jobs and exclusions (with reason codes) and does not dial.

**Acceptance criteria**
- [ ] `pre_due` selects instalments due in 0–3 days, `missed_payment` selects 1–30 DPD, and `callback` merges callbacks due with broken promises.
- [ ] Every candidate goes through the compliance gate; an exclusion records every failing rule, not just the first.
- [ ] Re-running a campaign does not duplicate a job that is still open for the same customer, loan and campaign.
- [ ] `campaigns.yaml` carries a `version` that is logged with each run.

**Tests**
- `tests/scheduler/test_campaigns.py` (unit, FakeGateway): persona fixtures give the expected candidate sets per campaign; out-of-range DPD is excluded.
- `tests/scheduler/test_gate.py` (unit, frozen clock): Sam is excluded for `cease_contact`, Dawit for `dispute_open`, a DNC customer for `dnc`, a customer at 21:30 local time for `outside_window`, and one with no consent for `no_consent`; multiple reasons are aggregated.
- `tests/scheduler/test_queue.py` (unit): de-duplication and status transitions.

**Files:** `src/supportagent/contracts/scheduler.py`, `src/supportagent/scheduler/config.py`, `src/supportagent/scheduler/gateway_client.py`, `src/supportagent/scheduler/campaigns.py`, `src/supportagent/scheduler/gate.py`, `src/supportagent/scheduler/queue.py`, `data/config/campaigns.yaml`, `tests/scheduler/conftest.py`, `tests/scheduler/test_campaigns.py`, `tests/scheduler/test_gate.py`, `tests/scheduler/test_queue.py`

### T-SCHED-02 · Dialer: rooms, SIP participant, pacing
- **Status:** todo
- **Milestone:** M3
- **Issue:** #55
- **Depends on:** T-SCHED-01, T-TEL-04
- **Requirements:** SCHED-R3

Add a `Dialer` loop that takes jobs whose `not_before` has passed. A semaphore of size
`SCHED_MAX_CONCURRENT` (validated as 1–2) limits concurrent calls. The loop calls
`telephony.dial(api, phone, CallMetadata(...))` and moves the job through `dialing` → `in_call` →
`completed`. The pacing slot is released on the room-finished signal, or after `MAX_CALL_S`.

**Acceptance criteria**
- [ ] Room metadata carries `call_id`, `campaign`, `customer_id` and `loan_id`; `call_id` is the one created at queue time.
- [ ] No more than `SCHED_MAX_CONCURRENT` calls are ever in flight; a value of 0 or ≥ 3 fails at settings validation.
- [ ] A dial exception or failure never crashes the loop; the job becomes `failed` or goes to retry handling with a logged reason.

**Tests**
- `tests/scheduler/test_dialer.py` (unit, FakeLiveKitSipApi + frozen clock): metadata correctness; maximum concurrency observed with 1 and 2 over 5 jobs; the slot is released on room-finished and on timeout; failure paths.

**Files:** `src/supportagent/scheduler/dialer.py`, `src/supportagent/common/settings.py`, `tests/scheduler/test_dialer.py`

### T-SCHED-03 · Retry rules and attempt logging
- **Status:** todo
- **Milestone:** M3
- **Issue:** #56
- **Depends on:** T-SCHED-02
- **Requirements:** SCHED-R4

Log every dial through `POST /service/attempts` (`call_id`, `campaign`, `customer_id`, `loan_id`,
`contact_result`, `sip_status`, `started_at`, `ended_at`). `RetryPolicy.next_attempt()` uses the
`campaigns.yaml` retry rules. Any proposed retry time is re-validated against the calling window
(shifted to the next opening) and against the frequency cap, which counts persisted attempts.

**Acceptance criteria**
- [ ] Attempts are logged for answered and unanswered calls alike, upserted by `call_id`.
- [ ] `no_answer`, `busy` and `voicemail` follow their delay and maximum; `wrong_number` is never retried.
- [ ] A retry that would breach the cap is dropped with an exclusion reason of `frequency_cap`.
- [ ] A retry that falls outside the window moves to the next window opening in the customer's timezone.

**Tests**
- `tests/scheduler/test_retries.py` (unit, frozen clock): the table of result × attempt number → next time or `None`; the cap interaction (the 8th attempt in 7 days is dropped); the window shift across midnight and DST in a non-UTC timezone.
- `tests/scheduler/test_dialer.py::test_attempt_logged` (unit): FakeGateway receives exactly one attempt per dial.

**Files:** `src/supportagent/scheduler/retries.py`, `src/supportagent/scheduler/dialer.py`, `src/supportagent/scheduler/gateway_client.py`, `tests/scheduler/test_retries.py`

### T-SCHED-04 · Dial-time re-check of contact flags
- **Status:** todo
- **Milestone:** M3
- **Issue:** #57
- **Depends on:** T-SCHED-02
- **Requirements:** SCHED-R5

Immediately before `telephony.dial`, re-fetch `GET /service/contact-preferences/{customer_id}` and
the recent attempts, then re-run the gate with the current time. If the call is blocked, set
`cancelled_by_gate` with reasons, create no attempt, and cancel any pending retries for that customer.

**Acceptance criteria**
- [ ] A cease, dispute, DNC or consent-withdrawn flag set after queueing cancels the call before any SIP traffic.
- [ ] A job queued at 20:55 whose dial slot comes at 21:01 local time is not dialled.
- [ ] If the preference fetch fails, the call is not placed: the scheduler fails closed with reason `gate_unavailable`.

**Tests**
- `tests/scheduler/test_dialer.py::test_recheck_*` (unit): flags flipped in FakeGateway between queue and dial → zero dials on FakeLiveKitSipApi; the window boundary; fail-closed when the gateway raises.

**Files:** `src/supportagent/scheduler/dialer.py`, `src/supportagent/scheduler/gate.py`, `tests/scheduler/test_dialer.py`

### T-SCHED-05 · Callbacks and missed-promise follow-ups
- **Status:** todo
- **Milestone:** M3
- **Issue:** #58
- **Depends on:** T-SCHED-02
- **Requirements:** SCHED-R6

`FollowUpPlanner` reads `GET /service/candidates?campaign=callback`, which returns due callback tasks
and broken promises (a promise date before today with no posted payment). It creates `callback` jobs with
`not_before = max(requested_time, next_window_open)` and puts `callback_id` or `promise_id` in the
call metadata, so the agent can open with context (T-AGENT-12).

**Acceptance criteria**
- [ ] A callback requested outside the window is moved to the next opening in the customer's local time.
- [ ] Each broken promise produces at most one follow-up job, idempotent across runs.
- [ ] Follow-ups pass the same gate as campaigns, at queue time and at dial time.

**Tests**
- `tests/scheduler/test_followups.py` (unit, frozen clock): the 07:30 → 08:00 shift; a promise due yesterday with no payment produces one job; a kept promise produces none; a second run produces nothing new; a gated customer is excluded.

**Files:** `src/supportagent/scheduler/followups.py`, `tests/scheduler/test_followups.py`

### T-SCHED-06 · Scheduler service: jobs, trigger API, compose service
- **Status:** todo
- **Milestone:** M3
- **Issue:** #59
- **Depends on:** T-SCHED-03, T-SCHED-04, T-SCHED-05
- **Requirements:** SCHED-R7

Wire `service.py`, run with `python -m supportagent.scheduler`:
- a FastAPI app with the routes in [spec.md](spec.md#interfaces), using a bearer `SCHEDULER_TOKEN`
- an `AsyncIOScheduler` with one job per enabled campaign (`run_every_min`), the follow-up planner
  every 5 minutes, and the dialer loop
- a LiveKit `room_finished` webhook route

Add the `scheduler` compose service (telephony profile, port 8003).

**Acceptance criteria**
- [ ] `POST /campaigns/{name}/run?dry_run=true` returns a `RunReport` and places no calls; without dry-run it queues jobs.
- [ ] `POST /calls` gates a single on-demand call and returns 409 with reasons when it is blocked.
- [ ] `GET /jobs` and `GET /exclusions` return what the console needs (T-CON-02).
- [ ] Requests without the bearer token get 401; `GET /healthz` needs no token.
- [ ] `docker compose --profile telephony up scheduler` starts and passes its healthcheck.

**Tests**
- `tests/scheduler/test_service.py` (unit, TestClient + fakes): each route works; auth is enforced; dry-run makes no dial; a blocked single call returns 409; the webhook releases the slot.
- `tests/scheduler/test_scheduler_integration.py` (integration): with the compose Postgres, the bank API and the gateway running, a `missed_payment` dry-run over the persona seed returns Frank and Hana and excludes Sam and Dawit with the correct reasons.

**Files:** `src/supportagent/scheduler/service.py`, `src/supportagent/scheduler/__main__.py`, `docker-compose.yml`, `.env.example`, `tests/scheduler/test_service.py`, `tests/scheduler/test_scheduler_integration.py`
