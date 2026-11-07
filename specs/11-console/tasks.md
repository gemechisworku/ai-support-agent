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

> **Replaced UI (2026-11-07):** this task built the Streamlit version. The console moved to Next.js +
> a console API (T-CON-09..14); T-CON-14 removes the Streamlit code. The text below records what was built.

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

> **Replaced UI (2026-11-07):** this task built the Streamlit version. The console moved to Next.js +
> a console API (T-CON-09..14); T-CON-14 removes the Streamlit code. The text below records what was built.

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

> **Replaced UI (2026-11-07):** this task built the Streamlit version. The console moved to Next.js +
> a console API (T-CON-09..14); T-CON-14 removes the Streamlit code. The text below records what was built.

**Acceptance criteria**
- [x] The CON-R9 acceptance criterion in spec.md holds against the fake gateway API (the live check with 20 ingested
  documents, and the "capital of France" part with real embeddings once T-VOICE-01 starts llama.cpp, are still to do)
- [x] Low-confidence results are shown with a visible "agent would offer a follow-up" note

**Tests**
- `tests/console/test_explorer_policies.py` (unit): `AppTest` with a fake gateway client; list, document view, search results and the low-confidence note
- `tests/console/test_clients.py` (unit): `GatewayServiceClient` paths and error mapping

**Files:** `src/supportagent/console/pages/policies.py`, `src/supportagent/console/clients.py`, tests above

### T-CON-08 · Persona test sheet and call audit trail
- **Status:** done
- **Milestone:** M2
- **Issue:** #98
- **Depends on:** T-CON-01, T-BANK-09
- **Requirements:** CON-R10

A "Test personas" page from `GET /personas` (situation, expected outcome, extension, and the
synthetic verification details clearly labelled as test identities), each linking to the customer
explorer; and an "Audit trail" page that takes a `call_id` (or picks a recent call from
`GET /interactions`) and lists `GET /audit?call_id=` rows in order: time, tool, decision, latency,
customer.

> **Replaced UI (2026-11-07):** this task built the Streamlit version. The console moved to Next.js +
> a console API (T-CON-09..14); T-CON-14 removes the Streamlit code. The text below records what was built.

**Acceptance criteria**
- [x] The CON-R10 acceptance criterion in spec.md holds against the fake bank API (the live check with the
  `call_id` printed by `scripts/demo_m1.py` is still to do)
- [x] Denied tool calls are highlighted

**Tests**
- `tests/console/test_explorer_personas_audit.py` (unit): `AppTest` with fake clients; 12 personas; audit rows ordered, denials highlighted

**Files:** `src/supportagent/console/pages/personas.py`, `src/supportagent/console/pages/audit.py`, tests above

### T-CON-09 · Console API (backend for the Next.js console)
- **Status:** todo
- **Milestone:** M2
- **Issue:** #102
- **Depends on:** T-CON-06, T-CON-07, T-CON-08
- **Requirements:** CON-R1, CON-R7, CON-R11

Add `supportagent.console.api:create_app(clients=None)`, a FastAPI app with the M2 routes listed in
[spec.md](spec.md#interfaces): overview, customers (search and detail), loans, policies (list,
document, search), personas and audit. Turn `views.py` into pydantic view models plus pure builders
that apply the masking (keep `mask_number` / `mask_reference`), reusing `clients.py` unchanged.
- Auth: every route except `/healthz` requires `Authorization: Bearer <CONSOLE_API_TOKEN>`.
- Errors: `ConsoleApiError` → `502 {"detail"}`, upstream 404 → `404`.
- Add settings `CONSOLE_API_URL` and `CONSOLE_API_TOKEN`, the compose service `console-api` on
  `127.0.0.1:8004`, and `scripts/export_console_openapi.py`, which writes `web/console/openapi.json`.
- The Streamlit app keeps working until T-CON-14.

**Acceptance criteria**
- [ ] Every route returns its view model against faked upstreams; Frank's detail has loan `•••• 4417`, 5 DPD and $200.72 overdue (the persona-seeded fake bank)
- [ ] An upstream that returns a full 16-digit number yields only the last 4 in every response that contains it
- [ ] Missing or wrong token → 401; upstream down → 502 with a safe message; unknown customer → 404
- [ ] `lint-imports` passes; the console module imports no bank/kb/gateway/agent/DB code
- [ ] `docker compose up -d --wait console-api` is healthy on `127.0.0.1:8004`; the new settings are in `.env.example`

**Tests**
- `tests/console/test_api.py` (unit): every route via `TestClient` with `MockTransport`-backed clients; auth, 401/404/502 mapping
- `tests/console/test_views.py` (unit): view builders and masking, including the 16-digit case
- `tests/console/test_openapi_export.py` (unit): the export script output equals the committed `web/console/openapi.json`

**Files:** `src/supportagent/console/api.py`, `src/supportagent/console/views.py`, `src/supportagent/common/settings.py`, `scripts/export_console_openapi.py`, `web/console/openapi.json`, `docker-compose.yml`, `.env.example`, `tests/console/`

### T-CON-10 · Next.js console scaffold, typed client, CI and Docker
- **Status:** todo
- **Milestone:** M2
- **Issue:** #103
- **Depends on:** T-CON-09
- **Requirements:** CON-R11, CON-R7

Create `web/console` (layout in [plan.md](plan.md#file-layout)):
- **Toolchain:** Next.js App Router, TypeScript strict, ESLint, Prettier, Tailwind, shadcn/ui; npm with a
  committed `package-lock.json`.
- **Typed client:** `src/lib/api/schema.ts` generated by `openapi-typescript` from `openapi.json`;
  `src/lib/api/client.ts` is `server-only`, reads `CONSOLE_API_URL` / `CONSOLE_API_TOKEN`, and turns
  failures into an `ApiError`.
- **Shell:** root layout with the synthetic-data banner, the navigation (Data explorer; Operations
  marked "coming in M4"), `error.tsx`, `loading.tsx`, and an overview page from `GET /api/overview`.
- **Shared components:** data table (TanStack), error panel, `lib/format.ts`.
- **Delivery:**
  - `docker/console.Dockerfile` (Node 24 LTS, standalone output, non-root)
  - compose service `console` on `127.0.0.1:8501`; the Streamlit service moves to `console-streamlit`
    (profile `legacy`) until T-CON-14
  - Make targets `console`, `console-dev`, `console-types`, `console-check` (wired into `make check`)
  - CI job "Console (Next.js)"; the docker job and the release workflow build and publish the image

**Acceptance criteria**
- [ ] `make console` serves the overview on `localhost:8501` with the banner and live counts from the console API
- [ ] With the console API stopped, the page shows an error panel naming the console API, not a stack trace
- [ ] `make console-check` (lint, typecheck, OpenAPI drift, Vitest, build, Playwright) passes locally and in CI
- [ ] No token or upstream URL appears in the client bundle (`.next/static` is grepped in the CI job)

**Tests**
- `web/console/tests/unit/format.test.ts`, `error-panel.test.tsx`, `client.test.ts` (Vitest)
- `web/console/tests/e2e/shell.spec.ts` (Playwright, fake console API): banner, nav, overview counts, console API down → error panel

**Files:** `web/console/**`, `docker/console.Dockerfile`, `docker-compose.yml`, `Makefile`, `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `.gitignore`, `.env.example`

### T-CON-11 · Customer and loan explorer in Next.js
- **Status:** todo
- **Milestone:** M2
- **Issue:** #104
- **Depends on:** T-CON-10
- **Requirements:** CON-R8, CON-R7

`/customers`: search box and segment/bucket filters as URL params over `GET /api/customers`, paginated
table linking to `/customers/[id]`. The customer page has tabs: Profile and contact preferences ·
Loans (computed status) · Schedule (paid/unpaid highlighted) · Transactions (pending flagged) ·
History (calls, open promises, open tasks). Every figure comes from the console API.

**Acceptance criteria**
- [ ] The CON-R8 acceptance criterion holds in Playwright against fixtures recorded from the persona-seeded console API
- [ ] Filters and the selected tab survive a reload (URL state)
- [ ] An unknown customer id shows a not-found state

**Tests**
- `web/console/tests/e2e/customers.spec.ts` (Playwright): search "Frank", open him, check the CON-R8 values in each tab
- `web/console/tests/unit/customer-tabs.test.tsx` (Vitest)

**Files:** `web/console/src/app/customers/`, `web/console/src/components/`, tests above

### T-CON-12 · Policy explorer and search playground in Next.js
- **Status:** todo
- **Milestone:** M2
- **Issue:** #105
- **Depends on:** T-CON-10
- **Requirements:** CON-R9

`/policies`: document table (product, audience, versions, effective dates, chunk counts);
`/policies/[docId]?version=`: each chunk's section path and rendered Markdown; `/policies/search`:
query and optional product, showing each chunk's citation (`doc_id@version#section`), RRF score,
similarity and the `confident` flag, with an "agent would offer a follow-up" note when not confident.

**Acceptance criteria**
- [ ] The CON-R9 acceptance criterion holds: 20 documents, the late-fees chunks, and confident / not-confident searches (Playwright on fixtures, plus a recorded live check against the llama.cpp embeddings)
- [ ] A failing document view shows an error panel while the search page keeps working

**Tests**
- `web/console/tests/e2e/policies.spec.ts` (Playwright): list, document, both searches
- `web/console/tests/unit/search-results.test.tsx` (Vitest): the low-confidence note

**Files:** `web/console/src/app/policies/`, `web/console/src/components/`, tests above

### T-CON-13 · Persona test sheet and audit trail in Next.js
- **Status:** todo
- **Milestone:** M2
- **Issue:** #106
- **Depends on:** T-CON-10
- **Requirements:** CON-R10, CON-R7

`/personas`: the 12 personas (situation, expected outcome, extension, and the synthetic verification
details labelled as test identities), each linking to its customer page. `/audit?call_id=`: a call id
input (or a recent-call picker) and the audit rows in order: time, tool, decision, latency, customer,
with denied calls highlighted.

**Acceptance criteria**
- [ ] The CON-R10 acceptance criterion holds (Playwright on fixtures; live check with the `scripts/demo_m1.py` call id)
- [ ] The persona sheet is the only page that renders a DOB, under a "synthetic test identity" label

**Tests**
- `web/console/tests/e2e/personas-audit.spec.ts` (Playwright): 12 personas, audit order, denial highlight, DOB only on `/personas`

**Files:** `web/console/src/app/personas/`, `web/console/src/app/audit/`, tests above

### T-CON-14 · Retire the Streamlit console
- **Status:** todo
- **Milestone:** M2
- **Issue:** #107
- **Depends on:** T-CON-11, T-CON-12, T-CON-13
- **Requirements:** CON-R1, CON-R11

Delete `src/supportagent/console/app.py`, `ui.py` and `pages/`, the Streamlit `AppTest` tests, the
`console-streamlit` compose service and the `streamlit` dependency. Update the README run section,
`docs/architecture.md`, `docs/dev-setup.md` and AGENTS.md so the console is only the Next.js app plus
the console API.

**Acceptance criteria**
- [ ] `grep -ri streamlit` finds nothing outside `uv.lock` history, `docs/design/` and the task history in this file
- [ ] `make check` passes; `uv.lock` no longer contains `streamlit`
- [ ] The README run steps start the Next.js console and console API, verified once on the dev Mac

**Tests**
- Existing console API and Next.js suites stay green; `tests/console/test_no_streamlit.py` (unit) asserts `streamlit` is not importable from the console package and not a project dependency

**Files:** `src/supportagent/console/`, `tests/console/`, `docker-compose.yml`, `pyproject.toml`, `uv.lock`, `README.md`, `docs/architecture.md`, `docs/dev-setup.md`, `AGENTS.md`

### T-CON-02 · Campaign and single-call triggers
- **Status:** todo
- **Milestone:** M4
- **Issue:** #68
- **Depends on:** T-CON-10, T-SCHED-06
- **Requirements:** CON-R2, CON-R3

Add the console API routes `POST /api/campaigns/{campaign}/run`, `POST /api/calls` and
`GET /api/calls/active`, then build the Next.js `/campaigns` page with three parts:
- **Campaign run:** pick a campaign (`pre_due`, `missed_payment`), then "Dry run" shows the eligible and
  excluded tables, with excluded rows carrying their gate reasons. "Start calls" is enabled only after
  an explicit confirm checkbox; the real run is a server action.
- **Single call:** a loan id input (or a persona picker listing persona loan ids) plus a campaign, which
  posts `/calls` and shows the `call_id` with a link to its detail page.
- **Live calls:** a client component that polls `/api/calls/active` every 5 s (through a Next.js route
  handler, so the token stays on the server).

Complete `SchedulerClient` against `contracts.scheduler`.

**Acceptance criteria**
- [ ] A dry run calls `POST /campaigns/{c}/run` with `{"dry_run": true}` and renders both tables. Stop-calling Sam's fixture row shows `cease_contact`.
- [ ] Real runs are impossible without the confirm checkbox: the button is disabled and no request is sent.
- [ ] A single call shows the returned `call_id` and a detail link.
- [ ] The live table refreshes and shows the empty state "No calls in progress".

**Tests**
- `tests/console/test_api_operations.py` (unit): the three routes over a fake scheduler transport, asserting the requests sent.
- `web/console/tests/unit/campaigns.test.tsx` (Vitest): confirm gate, excluded reasons, empty live state.
- `web/console/tests/e2e/campaigns.spec.ts` (Playwright): dry run, start disabled without confirm, single call shows `call_id`.

**Files:** `src/supportagent/console/api.py`, `src/supportagent/console/clients.py`, `src/supportagent/console/views.py`, `web/console/src/app/campaigns/`, `web/console/openapi.json`, `web/console/src/lib/api/schema.ts`, tests above

### T-CON-03 · Calls list and call detail
- **Status:** todo
- **Milestone:** M4
- **Issue:** #69
- **Depends on:** T-CON-10, T-OBS-03
- **Requirements:** CON-R3, CON-R4

Add console API routes `GET /api/calls` (paginated `GET /interactions` with filters: date range,
direction, campaign, contact_result, escalated) and `GET /api/calls/{call_id}` (call detail plus the
transcript from `TranscriptStore.read`). The Next.js `/calls` page has the filters as URL params; the
`/calls/[callId]` page has these sections:
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
- [ ] All identifiers are masked by the console API. The transcript is displayed as stored, already redacted.

**Tests**
- `tests/console/test_api_calls.py` (unit): filter params, pagination, detail with and without a transcript (tmp transcript dir via settings override).
- `web/console/tests/e2e/calls.spec.ts` (Playwright): pagination, every detail section, the missing-transcript warning.

**Files:** `src/supportagent/console/api.py`, `src/supportagent/console/views.py`, `web/console/src/app/calls/`, `web/console/openapi.json`, `web/console/src/lib/api/schema.ts`, tests above

### T-CON-04 · Follow-up queue
- **Status:** todo
- **Milestone:** M4
- **Issue:** #70
- **Depends on:** T-CON-10
- **Requirements:** CON-R5

Add console API routes `GET /api/followups` and `PATCH /api/tasks/{id}`. The Next.js `/followups` page
has three tabs:
- **Promises:** due today, upcoming, broken, from `GET /promises?status=`
- **Callbacks:** `GET /tasks?type=callback&status=open`
- **Escalations:** `GET /tasks?type=escalation&status=open`

Rows are ordered by due time and show reason, summary (redacted) and the linked call. "Assign to me"
and "Mark done" are server actions that send `PATCH /tasks/{id}` through the console API.

**Acceptance criteria**
- [ ] Each tab lists the right fixture rows in due order. Broken promises are highlighted.
- [ ] "Mark done" sends `PATCH /tasks/{id}` `{"status":"done"}` and the row disappears after the action revalidates the page.
- [ ] Each row links to its call detail.

**Tests**
- `tests/console/test_api_followups.py` (unit): tab queries and the recorded PATCH body.
- `web/console/tests/e2e/followups.spec.ts` (Playwright): tabs in due order, mark done removes the row.

**Files:** `src/supportagent/console/api.py`, `src/supportagent/console/clients.py`, `web/console/src/app/followups/`, `web/console/openapi.json`, `web/console/src/lib/api/schema.ts`, tests above

### T-CON-05 · Compliance overview
- **Status:** todo
- **Milestone:** M4
- **Issue:** #71
- **Depends on:** T-CON-03
- **Requirements:** CON-R6

The console API route `GET /api/compliance?from=&to=` returns a `ComplianceSummary`; the Next.js
`/compliance` page shows four metric cards for a date range:
- verified-before-data %
- disclosures %
- window and cap violations
- disputes and cease requests honoured

`console.compliance.compliance_summary(details, rules)` computes them in Python with `observability.guardrails` (T-EVAL-03)
and `compliance` window/cap functions (T-COMP-02/03; both are done in earlier milestones). A table
lists failing calls with links. The current contact-rules version comes from `CallDetail.versions.policy`.

**Acceptance criteria**
- [ ] With a clean fixture set the summary (and page) shows 100% / 100% / 0 / 0 and "all honoured".
- [ ] One call reading loan data before `verify_identity` → verified-first 50.0% (1 of 2), and that call is listed.
- [ ] A fixture outbound attempt at 21:30 customer local time counts as one window violation.
- [ ] An unhonoured cease request (a call after the flag) is listed.

**Tests**
- `tests/console/test_compliance.py` (unit): `compliance_summary` over crafted `CallDetail` fixtures for each metric and edge case; the route over fake upstreams.
- `web/console/tests/e2e/compliance.spec.ts` (Playwright): metric cards and failing-call links.

**Files:** `src/supportagent/console/compliance.py`, `src/supportagent/console/api.py`, `web/console/src/app/compliance/`, `web/console/openapi.json`, `web/console/src/lib/api/schema.ts`, tests above
