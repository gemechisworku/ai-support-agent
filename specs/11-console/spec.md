# 11 · Operations console — Spec

> **Code:** `CON` · **Location:** `src/supportagent/console/` · **Milestones:** M2 (data explorer), M4 (operations)
> **PRD:** F10, AC-GUARD, NFR-AUDIT, NFR-SEC · **Depends on:** BANK ops API (T-BANK-07), SCHED trigger API (T-SCHED-06), OBS transcripts · **Status:** see [tasks.md](tasks.md)

## Purpose
A small internal Streamlit UI for the operations/compliance lead and the collections officer (PRD §4).
They use it to:
- trigger campaigns and single calls
- watch live and past calls
- read redacted transcripts with tool calls, outcomes and sources
- work the follow-up queue
- confirm at a glance that verification, disclosures and contact rules held

It is a **thin client**: it holds no business logic and has no database access. It only calls the bank
ops API and the scheduler API over HTTP, reads redacted transcripts through `observability.transcripts`,
and reuses the pure checkers in `observability.guardrails` and `compliance`.

It also hosts a **data explorer** (M2) for the developer and the compliance lead: browse the mock
bank and the policy knowledge base, look up the scripted personas, and read the gateway audit trail of a
call — the context the agent works with — without querying a database.

## Scope
**In:**
- the Streamlit app and its pages
- typed HTTP clients for bank ops and scheduler
- the compose service `console` on `127.0.0.1:8501`

**Out:**
- computing eligibility (SCHED/COMP)
- storing anything (BANK)
- redaction itself (OBS)
- authentication: local-only POC, bound to localhost. Production auth is a follow-up.

## Requirements
- **CON-R1** (P0 · F10, NFR-SEC) — The console shall reach data only through typed clients: `BankOpsClient` for the bank API (read, T-BANK-07 ops and T-BANK-09 explorer endpoints), `GatewayServiceClient` for the gateway's T-GW-12 knowledge-base browse API and `SchedulerClient` for the T-SCHED-06 trigger API, plus `observability.transcripts`. It shall not import `bank`, `kb`, `gateway`, `agent` or a DB driver (import-linter).
- **CON-R2** (P0 · F10) — The console shall let an operator run a campaign (`pre_due`, `missed_payment`) as a dry run (show eligible accounts and excluded accounts with gate reasons) or for real, and place a single on-demand call for a loan. A confirmation step is required before any real dialling.
- **CON-R3** (P0 · F10) — The console shall list live calls (from the scheduler's active-calls endpoint, auto-refreshing every 5 s) and past calls (paginated, filterable by date range, direction, campaign, contact result and escalated).
- **CON-R4** (P0 · F10, NFR-AUDIT) — The call detail page shall show the outcome fields (PRD §8), the redacted transcript in turn order, tool calls from the audit log (tool, decision, latency), KB sources (`doc_id@version`, section), versions, latency p50/p90 and the recording path, and shall flag any missing artefact.
- **CON-R5** (P0 · F10) — The follow-up queue shall list open promises (due today, upcoming, broken), callbacks and escalations, ordered by due time, and let an officer mark a task done or assigned.
- **CON-R6** (P0 · F10, AC-GUARD) — A compliance overview shall show, for a date range: % of calls with account data where verification came first, % of calls with all required disclosures, the count of calls outside the calling window or over the frequency cap (expected 0), and the count of dispute and cease-contact requests and whether each was honoured. Every failing call links to its detail page.
- **CON-R7** (P0 · NFR-SEC) — The console shall display only masked or redacted data (masked numbers, redacted transcripts, no DOB — except the persona test sheet's labelled synthetic test identities, CON-R10), bind to localhost only, and show a visible "synthetic data" banner.
- **CON-R8** (P0 · F10, F5) — A customer explorer shall search customers (by name, phone, customer id or loan last 4; filter by segment and delinquency bucket) and show one customer's profile, contact preferences, loans with computed status (balance, DPD, bucket, next due), instalment schedule, recent transactions (pending flagged), and interaction history with open promises and tasks.
- **CON-R9** (P0 · F10, F6, U4) — A policy explorer shall list the knowledge-base documents (title, product, audience, versions and effective dates, chunk count), show a document version's chunks with their section paths, and offer a search playground that runs the same retrieval as the agent's `search_policy` and shows each returned chunk's citation, scores and the `confident` flag.
- **CON-R10** (P0 · F10, F11, NFR-AUDIT) — A persona test sheet shall list the 12 scripted personas with their situation, expected outcome, softphone extension and the verification details a tester needs to role-play them (full name, DOB, loan last 4), and an audit-trail view shall show every gateway tool call of a given `call_id` (tool, decision, latency, customer) in order.

## Acceptance criteria
- **CON-R1:** `lint-imports` passes with the console contract. With both APIs faked by
  `httpx.MockTransport`, every page renders in Streamlit `AppTest` without network.
- **CON-R2:** A dry run shows a table of eligible accounts and an "excluded" table whose `reasons` include
  e.g. `cease_contact` for Stop-calling Sam. "Start calls" is disabled until the confirm checkbox is ticked.
  A single call posts `{loan_id, campaign}` and shows the returned `call_id`.
- **CON-R3:** Given 120 fake interactions, the list paginates by 50, and the filters translate into the
  documented query params.
- **CON-R4:** For a fake call, the page shows each section. With the transcript file absent, it shows
  "Transcript missing" instead of failing.
- **CON-R5:** "Mark done" sends `PATCH /tasks/{id}` with `{"status": "done"}` and the row disappears on rerun.
- **CON-R6:** Given fixtures with one call that read loan data before `verify_identity`, verified-first
  shows 50.0% with that call linked. Fixtures with no violations show 100% / 100% / 0 / 0.
- **CON-R7:** Rendering a page whose fake API returns a full 16-digit number still shows only the last 4.
- **CON-R8:** Searching "Frank" lists Forgetful Frank; his page shows loan •••• 4417 at 5 DPD with $200.72 overdue
  since 4 Nov 2026, the instalment schedule and his transactions — values equal to the bank API's.
- **CON-R9:** The policy list shows 20 documents; opening `late-fees-penalty-interest` shows its chunks; searching
  "late fee personal loan" returns cited chunks from the fees documents with `confident=true`, and "capital of
  France" with the llama.cpp embeddings returns `confident=false`.
- **CON-R10:** The persona sheet lists all 12 personas from `GET /personas`; entering the `call_id` printed by
  `scripts/demo_m1.py` shows its 5 audit rows in order. The persona sheet is the only place a DOB is shown
  (synthetic test identities), labelled as such.
  The container's port is published as `127.0.0.1:8501:8501`.

## Interfaces
**Consumes:**
- Bank ops (T-BANK-07): `GET /interactions`, `GET /interactions/{call_id}` (`CallDetail`), `GET /tasks`,
  `GET /promises`, `GET /audit?call_id=`, `GET /outbox`, `PATCH /tasks/{id}`. Models come from `contracts/bank.py`.
- Bank explorer (T-BANK-09) and read routes: `GET /customers/search?q=&segment=&bucket=` (`Page[CustomerSummary]`;
  not `GET /customers?q=`, which is the gateway's phone lookup), `GET /loans?bucket=&product=`, `GET /personas`,
  `GET /customers/{id}`, `/customers/{id}/loans`, `/customers/{id}/contact-preferences`,
  `/customers/{id}/interactions` (`InteractionHistory`), `GET /loans/{id}`, `/loans/{id}/installments`,
  `/accounts/{id}/transactions?loan_id=&limit=`. Header `X-Service-Token`.
- Gateway service API (T-GW-12, `Authorization: Bearer <service token>`, base URL = `GATEWAY_URL` without `/mcp`):
  `GET /service/kb/documents`, `GET /service/kb/documents/{doc_id}?version=`, `POST /service/kb/search`
  (`PolicyQuery` → `PolicySearchResult`). Models come from `contracts/kb.py`.
- Scheduler (T-SCHED-06): `POST /campaigns/{campaign}/run` (`{dry_run}` → `CampaignRunResult`),
  `POST /calls` (`{loan_id, campaign}` → `{call_id}`), `GET /calls/active` (`list[ActiveCall]`).
  Models come from `contracts/scheduler.py`.
- `observability.transcripts.TranscriptStore.read`, `observability.guardrails` (T-EVAL-03),
  and `compliance` window/cap functions (T-COMP-02/03).

**Provides:** `streamlit run src/supportagent/console/app.py` (compose service `console`, port 8501).

## Non-functional
- Each page renders in < 1 s against a seeded bank (pages fetch with `limit ≤ 200`; responses are
  cached with `st.cache_data(ttl=5)`).
- No writes besides the trigger, single-call and task-status actions.

## Open questions
- None blocking. Live audio monitoring is out of scope; the recording path is shown for post-call listening.
