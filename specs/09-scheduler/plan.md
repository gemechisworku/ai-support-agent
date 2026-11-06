# 09 · Outreach scheduler — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md).

## Approach
A small asyncio service. APScheduler's `AsyncIOScheduler` runs the periodic jobs, and FastAPI exposes
the trigger API, both in one process.

- **Selection.** A `CandidateSource` calls the gateway's service-scope API (architecture §2: "the
  scheduler asks the gateway (service scope) for accounts 1–30 days past due") and returns typed
  `Candidate` objects.
- **Gating.** Each candidate is converted into the compliance input model and passed to the
  T-COMP-04 gate, using a clock-injected `now`. Allowed candidates become `CallJob`s in an in-process
  queue, persisted only through the attempt log, which is the source of truth for the frequency cap.
  Rejected candidates become `Exclusion` records, kept in memory for the API and logged as structured
  events.
- **Dialling.** A `Dialer` worker loop takes jobs while a semaphore (`SCHED_MAX_CONCURRENT`) has
  capacity. It re-checks the gate with freshly fetched contact preferences, then calls
  `telephony.dial()` and logs the attempt.
  - For answered calls the agent owns the rest of the call. The dialer releases its slot when the
    room closes, using the LiveKit `room_finished` webhook, or polling `list_rooms` with a
    `MAX_CALL_S` timeout.
  - Unanswered calls go to `RetryPolicy`.
- **Follow-ups.** `FollowUpPlanner` turns due callbacks and broken promises into `callback` jobs, at
  the requested time or the next window opening (computed with compliance's window helper).

Time is always injected (`supportagent.common.clock`), so every rule is testable with a frozen clock.

## File layout
```
src/supportagent/scheduler/
  __init__.py
  config.py        # CampaignConfig/RetryPolicy loaded from data/config/campaigns.yaml
  gateway_client.py# service-scope HTTP client (httpx, bearer token)
  campaigns.py     # CandidateSource per campaign → list[Candidate]
  gate.py          # adapter: Candidate → compliance input, returns allow | Exclusion
  queue.py         # JobQueue (dedupe by customer_id+loan_id+campaign), job states
  dialer.py        # Dialer loop, pacing semaphore, dial-time re-check
  retries.py       # RetryPolicy.next_attempt(job, result, history) -> datetime | None
  followups.py     # FollowUpPlanner (callbacks due, broken promises)
  service.py       # FastAPI app + AsyncIOScheduler wiring; `python -m supportagent.scheduler`
src/supportagent/contracts/scheduler.py
data/config/campaigns.yaml
tests/scheduler/
  conftest.py (FakeGateway, FakeLiveKitSipApi, frozen clock)
  test_campaigns.py test_gate.py test_dialer.py test_retries.py test_followups.py test_service.py
  test_scheduler_integration.py   # marker integration (gateway+bank in compose)
```

## Data model / contracts
- `CampaignName = Literal["pre_due", "missed_payment", "callback"]`.
- `Candidate`: `customer_id`, `loan_id`, `phone`, `timezone`, `contact_prefs`, `attempts_7d`,
  `last_conversation_at`, `context` (for example `promise_id`, `callback_id`).
- `CallJob`: `job_id`, `call_id` (UUID4, created at queue time), `campaign`, `customer_id`, `loan_id`,
  `not_before`, `attempt_no`, `status`, `reasons[]`.
- `JobStatus`: `queued | dialing | in_call | completed | retry_scheduled | cancelled_by_gate | failed`.
- `Exclusion`: `customer_id`, `loan_id`, `campaign`, `reasons: list[str]` (compliance reason codes), `at`.
- `campaigns.yaml` (versioned): per campaign `enabled`, `run_every_min`, `selector` (`due_in_days: [0,3]`
  or `dpd: [1,30]`), and `retry` (`no_answer: {delay_min: 120, max: 2}`, `busy: {delay_min: 30, max: 2}`,
  `voicemail: {delay_min: 1440, max: 1}`, `wrong_number: {max: 0}`).

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| Candidate source | Gateway service scope, not the bank | Architecture §2; keeps one audited data path |
| Gate timing | At queue time **and** dial time | Flags set mid-queue are honoured immediately (F8) |
| Queue persistence | In-process plus the attempt log in the bank | Simple for the POC; the cap counts persisted attempts |
| Call end detection | LiveKit `room_finished` webhook, with a timeout fallback | Frees the pacing slot reliably |
| Service port | 8003 | Next free port after the bank API and the gateway |

## Dependencies
`apscheduler>=3.10,<4` (3.x has a stable asyncio API), `httpx`, `fastapi`, `uvicorn`,
`pyyaml`, plus `livekit-api` through `supportagent.telephony`. Adds a compose service `scheduler`
(`<<: *app`, `command: python -m supportagent.scheduler`, port 8003, telephony profile). New settings:
`SCHEDULER_TOKEN`, `SCHED_MAX_CONCURRENT`, `MAX_CALL_S`, `SCHEDULER_URL`.

## Test strategy
- **unit:** All logic runs against `FakeGateway` (in-memory candidates, preferences and attempt log),
  `FakeLiveKitSipApi` (records dials, scripted outcomes) and a frozen clock.
  - Campaign selection.
  - Gate adapter reasons.
  - Pacing: the maximum number of concurrent calls observed.
  - Retry schedule and cap interaction.
  - Dial-time cancellation.
  - Callback window shift and broken-promise detection.
  - API via FastAPI `TestClient`.
- **integration:** With compose Postgres, the bank API and the gateway running in-process, a dry-run
  of `missed_payment` over the persona seed returns Frank and Hana and excludes Sam and Dawit.
- **live:** Covered by the telephony checklist (T-TEL-05) and the demo runbook (T-EVAL-06).

## Risks
- Webhook delivery from LiveKit to a containerised scheduler on macOS: use `host.docker.internal`, with
  polling as the fallback.
- Cap counting depends on the bank recording attempts by `call_id`. Interactions must be upserted by
  `call_id`, so the agent's outcome and the scheduler's attempt do not double count.
