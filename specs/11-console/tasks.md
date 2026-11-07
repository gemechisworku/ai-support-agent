# 11 · Operations console — Tasks

> Each task is one GitHub issue and one PR. The field bullets are parsed by `scripts/tasks.py`, so keep
> their exact format. Status is `todo` or `done`. Issue is `—` until `make issues-sync` fills it in.
> Ids are never reused. Order tasks by dependency.

## Tasks

### T-CON-01 · Console skeleton and bank API client
- **Status:** done
- **Milestone:** M2
- **Issue:** #67
- **Depends on:** T-BANK-07
- **Requirements:** CON-R1, CON-R7

Create the Streamlit app shell (`app.py` with `st.navigation`, a synthetic-data banner and placeholder
pages) and the typed clients in `clients.py`:
- `BankOpsClient`, covering the bank read, T-BANK-07 ops (incl. `PATCH /tasks/{id}`) and T-BANK-09 explorer endpoints
- `GatewayServiceClient` for the T-GW-12 browse API (built here so the `Clients` bundle is complete; T-CON-07 uses it)
- `SchedulerClient`, a stub against the `contracts.scheduler` shapes that is filled in by T-CON-02

Also add `get_clients()` injection ([plan.md](plan.md#approach)), `views.mask_number()`, the compose
service `console` bound to `127.0.0.1:8501`, and `make console`. Add a console contract to
import-linter if the existing "scheduler and console" contract does not already cover it.

**Acceptance criteria**
- [ ] `make console` serves the app on localhost:8501, and every page shows the banner. *(Banner on every page: proven by
  `test_shell_renders` in CI. The live `make console` run was not done: the runtime image can't be rebuilt on the
  build Mac's network; verify on first use.)*
- [x] Clients return `contracts` models and map HTTP errors to a `ConsoleApiError` that is shown as `st.error`, never as a traceback.
- [x] `mask_number("4401228733101234") == "•••• 1234"`.
- [x] `lint-imports` passes. No `bank`/`kb`/`gateway`/`agent`/DB imports in `console`.

**Tests**
- `tests/console/test_clients.py` (unit): paths, params, pagination and error mapping via `httpx.MockTransport`.
- `tests/console/test_app_pages.py::test_shell_renders` (unit): `AppTest.from_file(".../app.py").run()` has no exceptions and contains the banner.
- `tests/console/test_views.py::test_mask_number` (unit).

**Files:** `src/supportagent/console/app.py`, `src/supportagent/console/clients.py`, `src/supportagent/console/views.py`, `src/supportagent/console/pages/*.py`, `docker-compose.yml`, `Makefile`, `.env.example`, `tests/console/conftest.py`, `tests/console/test_clients.py`, `tests/console/test_app_pages.py`, `tests/console/test_views.py`

### T-CON-02 · Campaign and single-call triggers
- **Status:** todo
- **Milestone:** M4
- **Issue:** #68
- **Depends on:** T-CON-01, T-SCHED-06
- **Requirements:** CON-R2, CON-R3

Build the Campaigns page with three parts:
- **Campaign run:** pick a campaign (`pre_due`, `missed_payment`), then "Dry run" shows the eligible and
  excluded tables, with excluded rows carrying their gate reasons. "Start calls" is enabled only after
  an explicit confirm checkbox.
- **Single call:** a loan id input (or a persona picker listing persona loan ids) plus a campaign, which
  posts `/calls` and shows the `call_id` with a link to its detail page.
- **Live calls:** an `st.fragment(run_every=5)` table from `GET /calls/active`.

Complete `SchedulerClient` against `contracts.scheduler`.

**Acceptance criteria**
- [ ] A dry run calls `POST /campaigns/{c}/run` with `{"dry_run": true}` and renders both tables. Stop-calling Sam's fixture row shows `cease_contact`.
- [ ] Real runs are impossible without the confirm checkbox: the button is disabled and no request is sent.
- [ ] A single call shows the returned `call_id` and a detail link.
- [ ] The live table refreshes and shows the empty state "No calls in progress".

**Tests**
- `tests/console/test_app_pages.py::test_campaign_dry_run`, `::test_start_requires_confirm`, `::test_single_call`, `::test_live_calls_empty` (unit, AppTest + fake transport; asserts requests sent).

**Files:** `src/supportagent/console/pages/campaigns.py`, `src/supportagent/console/clients.py`, `tests/console/test_app_pages.py`

### T-CON-03 · Calls list and call detail
- **Status:** todo
- **Milestone:** M4
- **Issue:** #69
- **Depends on:** T-CON-01, T-OBS-03
- **Requirements:** CON-R3, CON-R4

The Calls page is a paginated `GET /interactions` with filters (date range, direction, campaign,
contact_result, escalated); the filter-to-param mapping lives in `views`. The Call detail page
(`?call_id=`) has these sections:
- outcome fields from PRD §8
- versions
- latency p50/p90
- the transcript from `TranscriptStore.read`, rendered as chat bubbles with state and tool chips
- tool calls from audit rows (tool, decision, latency)
- KB sources
- the recording path

Missing artefacts are shown as warnings.

**Acceptance criteria**
- [ ] 120 fake interactions → 3 pages of 50/50/20, with the filters sent as query params.
- [ ] The detail page renders every section for a complete fixture call.
- [ ] A missing transcript or recording shows a warning, not an exception.
- [ ] All identifiers are masked. The transcript is displayed as stored, already redacted.

**Tests**
- `tests/console/test_views.py::test_filter_params` (unit).
- `tests/console/test_app_pages.py::test_calls_pagination`, `::test_call_detail_sections`, `::test_call_detail_missing_transcript` (unit, AppTest; tmp transcript dir via settings override).

**Files:** `src/supportagent/console/pages/calls.py`, `src/supportagent/console/pages/call_detail.py`, `src/supportagent/console/views.py`, `tests/console/test_app_pages.py`, `tests/console/test_views.py`

### T-CON-04 · Follow-up queue
- **Status:** todo
- **Milestone:** M4
- **Issue:** #70
- **Depends on:** T-CON-01
- **Requirements:** CON-R5

The Follow-ups page has three tabs:
- **Promises:** due today, upcoming, broken, from `GET /promises?status=`
- **Callbacks:** `GET /tasks?type=callback&status=open`
- **Escalations:** `GET /tasks?type=escalation&status=open`

Rows are ordered by due time and show reason, summary (redacted) and the linked call. "Assign to me"
and "Mark done" actions send `PATCH /tasks/{id}`.

**Acceptance criteria**
- [ ] Each tab lists the right fixture rows in due order. Broken promises are highlighted.
- [ ] "Mark done" sends `PATCH /tasks/{id}` `{"status":"done"}` and the row disappears after rerun.
- [ ] Each row links to its call detail.

**Tests**
- `tests/console/test_app_pages.py::test_followups_tabs`, `::test_mark_task_done` (unit, AppTest + fake transport recording the PATCH).

**Files:** `src/supportagent/console/pages/followups.py`, `src/supportagent/console/clients.py`, `tests/console/test_app_pages.py`

### T-CON-05 · Compliance overview
- **Status:** todo
- **Milestone:** M4
- **Issue:** #71
- **Depends on:** T-CON-03
- **Requirements:** CON-R6

The Compliance page shows four metrics (`st.metric`) for a date range:
- verified-before-data %
- disclosures %
- window and cap violations
- disputes and cease requests honoured

`views.compliance_summary(details, rules)` computes them with `observability.guardrails` (T-EVAL-03)
and `compliance` window/cap functions (T-COMP-02/03; both are done in earlier milestones). A table
lists failing calls with links. The current contact-rules version comes from `CallDetail.versions.policy`.

**Acceptance criteria**
- [ ] With a clean fixture set the page shows 100% / 100% / 0 / 0 and "all honoured".
- [ ] One call reading loan data before `verify_identity` → verified-first 50.0% (1 of 2), and that call is listed.
- [ ] A fixture outbound attempt at 21:30 customer local time counts as one window violation.
- [ ] An unhonoured cease request (a call after the flag) is listed.

**Tests**
- `tests/console/test_compliance_view.py` (unit): `compliance_summary` over crafted `CallDetail` fixtures for each metric and edge case.
- `tests/console/test_app_pages.py::test_compliance_page` (unit, AppTest).

**Files:** `src/supportagent/console/pages/compliance.py`, `src/supportagent/console/views.py`, `tests/console/test_compliance_view.py`, `tests/console/test_app_pages.py`

### T-CON-06 · Customer and loan explorer
- **Status:** done
- **Milestone:** M2
- **Issue:** #96
- **Depends on:** T-CON-01, T-BANK-09
- **Requirements:** CON-R8, CON-R7

A "Customers" page: search box and segment/bucket filters over `GET /customers/search` (paginated table),
then a customer page with tabs: Profile and contact preferences · Loans (computed status per loan) ·
Schedule (instalments, paid/unpaid highlighted) · Transactions (pending flagged) · History (past
calls, open promises, open tasks). Numbers are masked with `views.mask_number()`.

**Acceptance criteria**
- [x] The CON-R8 acceptance criterion in spec.md holds (AppTest against the persona-seeded fake bank, whose
  figures come from the bank's own ledger; the live run against the seeded bank is still to do)
- [x] Every figure comes from the bank API response; the page computes nothing
- [x] An API error shows `st.error`, never a traceback

**Tests**
- `tests/console/test_explorer_customers.py` (unit): `AppTest` with a fake `BankOpsClient` built from the persona seed; search, tabs, masking

**Files:** `src/supportagent/console/pages/customers.py`, `src/supportagent/console/clients.py`, tests above

### T-CON-07 · Policy knowledge-base explorer and search playground
- **Status:** done
- **Milestone:** M2
- **Issue:** #97
- **Depends on:** T-CON-01, T-GW-12
- **Requirements:** CON-R9

A "Policies" page: document table (product, audience, versions, effective dates, chunk counts) from
`GET /service/kb/documents`; a document view rendering each chunk's section path and Markdown; and a
search playground (query, optional product) calling `POST /service/kb/search`, showing each chunk's
citation (`doc_id@version#section`), RRF score, similarity and the `confident` flag — what the agent
would be allowed to say. Uses the typed `GatewayServiceClient` (added with T-CON-01). A failing document
view shows `st.error` but keeps the search playground usable.

**Acceptance criteria**
- [x] The CON-R9 acceptance criterion in spec.md holds against the fake gateway API (the live check with 20 ingested
  documents, and the Ollama "capital of France" part once T-VOICE-01 installs Ollama, are still to do)
- [x] Low-confidence results are shown with a visible "agent would offer a follow-up" note

**Tests**
- `tests/console/test_explorer_policies.py` (unit): `AppTest` with a fake gateway client; list, document view, search results and the low-confidence note
- `tests/console/test_clients.py` (unit): `GatewayServiceClient` paths and error mapping

**Files:** `src/supportagent/console/pages/policies.py`, `src/supportagent/console/clients.py`, tests above

### T-CON-08 · Persona test sheet and call audit trail
- **Status:** todo
- **Milestone:** M2
- **Issue:** #98
- **Depends on:** T-CON-01, T-BANK-09
- **Requirements:** CON-R10

A "Test personas" page from `GET /personas` (situation, expected outcome, extension, and the
synthetic verification details clearly labelled as test identities), each linking to the customer
explorer; and an "Audit trail" page that takes a `call_id` (or picks a recent call from
`GET /interactions`) and lists `GET /audit?call_id=` rows in order: time, tool, decision, latency,
customer.

**Acceptance criteria**
- [ ] The CON-R10 acceptance criterion in spec.md holds
- [ ] Denied tool calls are highlighted

**Tests**
- `tests/console/test_explorer_personas_audit.py` (unit): `AppTest` with fake clients; 12 personas; audit rows ordered, denials highlighted

**Files:** `src/supportagent/console/pages/personas.py`, `src/supportagent/console/pages/audit.py`, tests above
