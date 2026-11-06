# 09 · Outreach scheduler — Spec

> **Code:** `SCHED` · **Location:** `src/supportagent/scheduler/` · **Milestones:** M3
> **PRD:** U1, U2, U5, F3, F8, F10, NFR-CAP · **Depends on:** COMP (gate library), GW (service-scope API), TEL (dial helper) · **Status:** see [tasks.md](tasks.md)

## Purpose
Decides **who** to call and **when**, and places the calls. It builds the candidate lists for the
outbound campaigns, filters every candidate through the compliance gate *before a call exists*,
paces dialling within the Mac's capacity, retries unanswered calls by rule, and runs callbacks and
missed-promise follow-ups. This is the main enforcement point for contact rules, so the model can
never override them (architecture §1).

## Scope
**In:** campaign definitions; candidate queue; compliance gate application; dial-time re-check;
dialling through `supportagent.telephony`; pacing; retries and attempt logging; callbacks and broken
promises; APScheduler jobs; a small HTTP trigger API for the console; the compose service.
**Out:**
- rule logic: window, cap, DNC, consent (`05-compliance`)
- eligibility SQL and storage (`02-mock-bank` through `04-gateway`)
- what happens on the call (`06-agent`)
- SIP and room mechanics (`08-telephony`)
- the console UI (`11-console`)

## Requirements
- **SCHED-R1** (P0 · U1, U2, U5, F3) — The scheduler shall define three campaigns and build their candidate lists from the gateway's service-scope endpoint (not the bank directly):
  - `pre_due`: an instalment due in 0–3 days, no payment posted
  - `missed_payment`: 1–30 DPD
  - `callback`: callbacks due, plus broken promises
- **SCHED-R2** (P0 · F8) — Every candidate shall pass `supportagent.compliance` (calling window in the customer's local time, frequency cap, DNC, consent, open dispute, cease contact) before it is queued. Each exclusion shall be recorded with its reason codes.
- **SCHED-R3** (P0 · F3, NFR-CAP) — The dialer shall create the call through `telephony.dial` with metadata `{call_id, campaign, customer_id, loan_id}`. At most `SCHED_MAX_CONCURRENT` calls (default 1, maximum 2) may be in progress at once.
- **SCHED-R4** (P0 · F3, F8) — Every dial attempt shall be logged through the gateway service API with its `contact_result`, and attempts shall count toward the frequency cap. No-answer, busy and voicemail shall be retried per a configured policy that never breaches the cap or the window; `wrong_number` is never retried.
- **SCHED-R5** (P0 · F8) — Immediately before each dial, the scheduler shall re-fetch the candidate's contact flags and re-run the gate. A cease-contact, dispute, DNC or consent change recorded at any time before the dial shall cancel the call.
- **SCHED-R6** (P0 · U5) — The scheduler shall place callbacks at the customer's requested time, or the next allowed time inside the calling window. It shall create a follow-up call for each promise-to-pay whose date has passed without a posted payment, with `campaign="callback"` and the promise id in the metadata.
- **SCHED-R7** (P0 · F3, F10) — The scheduler shall run as a service with periodic jobs and an HTTP API:
  - run a campaign now, with a dry-run option
  - place a single call on demand
  - list jobs and their status
  - list exclusions and their reasons

  The console uses this API.

## Acceptance criteria
- **SCHED-R1:** Given gateway fixtures for Olivia (due in 2 days), Frank (5 DPD) and Hana (22 DPD), `pre_due` yields Olivia, `missed_payment` yields Frank and Hana, and a 45-DPD loan is excluded.
- **SCHED-R2:** Given Stop-calling Sam (`cease_contact`), Disputing Dawit (`dispute_open`) and a customer whose local time is 21:30, none is queued, and each exclusion lists its reason codes.
- **SCHED-R3:** With `SCHED_MAX_CONCURRENT=1` and 3 queued jobs, the fake LiveKit API never sees more than one active call. A config value of 3 is rejected.
- **SCHED-R4:** A `no_answer` is retried after the configured delay. A third no-answer within 7 days of 6 prior attempts is not dialled, because the cap is reached. `wrong_number` creates no retry.
- **SCHED-R5:** A cease flag set between queueing and dialling cancels the job, with status `cancelled_by_gate` and the reasons recorded.
- **SCHED-R6:** A callback requested for 07:30 local time is scheduled at the window open time (08:00). A promise dated yesterday with no posted payment produces exactly one follow-up job.
- **SCHED-R7:** `POST /campaigns/missed_payment/run?dry_run=true` returns the would-call and excluded lists without dialling. `POST /calls` dials one customer through the same gate, and a blocked request returns 409 with the reasons.

## Interfaces
**Provides:**
- HTTP (port 8003, service token `SCHEDULER_TOKEN`):
  - `POST /campaigns/{name}/run?dry_run=`
  - `POST /calls` `{customer_id, loan_id, campaign}`
  - `GET /jobs?status=`
  - `GET /jobs/{job_id}`
  - `GET /exclusions?campaign=&since=`
  - `GET /healthz`
- Python: `Scheduler.run_campaign(name, dry_run)`, `Dialer`, `RetryPolicy`, `FollowUpPlanner`.
- Contracts (`contracts/scheduler.py`): `CampaignName`, `CallJob`, `JobStatus`, `Exclusion`, `RunReport`.

**Consumes:**
- Gateway service scope (T-GW-10), bearer `GATEWAY_SERVICE_TOKEN`:
  - `GET /service/candidates?campaign=` returns candidates with phone, timezone, contact preferences and recent attempts/conversations
  - `GET /service/contact-preferences/{customer_id}`
  - `POST /service/attempts`
  - `GET /service/candidates?campaign=callback` returns due callback tasks and broken promises (with `task_id`)
- `supportagent.compliance`: the gate function from T-COMP-04, with the `ContactRules` config.
- `supportagent.telephony.dial` and `LiveKitSipApi` (T-TEL-04).

## Non-functional
- NFR-CAP: pacing defaults to 1 concurrent call (16 GB Mac), with 2 allowed.
- Zero calls outside the window or over the cap (AC-GUARD). The gate runs twice, at queue time and dial time.
- Idempotent: running a campaign twice does not double-queue a candidate that already has an open job.
- No PII in logs: log `customer_id`, `call_id` and reasons only.

## Open questions
- Retry delays are POC defaults (no_answer 2 h, busy 30 min, voicemail next day; at most 2 retries per campaign per day) and are set in `data/config/campaigns.yaml`. To be confirmed with the compliance lead.
