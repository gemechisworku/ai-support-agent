# 11 · Operations console — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md).

## Approach
```
Browser ──> Next.js console (web/console, 127.0.0.1:8501)
              server components fetch; server actions write; no token reaches the browser
                 │  typed fetch (schema.ts generated from the console API's OpenAPI)
                 ▼
            Console API (supportagent.console, FastAPI, 127.0.0.1:8004)
              masking · view models · compliance aggregation (reuses observability + compliance)
                 │  HTTP only (import-linter)
                 ▼
            bank API :8001 · gateway /service :8002 · scheduler :8003 · var/transcripts (read-only)
```

**Console API (Python).**
- `create_app(clients=None)` is a FastAPI factory. Tests pass a `Clients` bundle backed by
  `httpx.MockTransport`; production builds it from settings (the existing `clients.build_clients`).
- Routes are thin: call `clients`, convert to a view model in `views.py`, return it. Masking
  (`mask_number`, `mask_reference`) happens in the view builders, so nothing unmasked leaves Python.
- Views keep typed values (`Decimal` as JSON string, ISO dates, enums); display formatting (currency,
  "4 Nov 2026") is the UI's job. Masked identifiers are plain strings.
- `ConsoleApiError` → `502 {"detail": msg}`; a missing resource upstream (404) → `404`.
- Auth: a dependency compares `Authorization: Bearer` with `CONSOLE_API_TOKEN` (constant-time).
- OpenAPI is the contract. `scripts/export_console_openapi.py` writes `web/console/openapi.json`;
  `openapi-typescript` turns it into `web/console/src/lib/api/schema.ts`. Both are committed, and CI
  regenerates them and fails on a diff.

**Next.js web app (TypeScript).**
- App Router with Cache Components (the `create-next-app` default): each page is a static shell
  (title, description) plus data components inside `<Suspense>`. Data components call the typed
  client (`src/lib/api/client.ts`, `import "server-only"`, built on `openapi-fetch`), which reads
  `CONSOLE_API_URL` and `CONSOLE_API_TOKEN` from the server environment, awaits `connection()` (so data
  is never prerendered at build time) and fetches with `cache: "no-store"`.
- Expected errors: `load(promise)` turns an `ApiError` into a value and the page renders
  `<ErrorPanel>` (production builds hide server error messages from `error.tsx`, which only handles
  unexpected failures). An upstream 404 → `notFound()`.
- Interactivity (filters, search box, tabs) uses URL search params, so every view is linkable
  (`/customers?q=frank`, `/customers/17`, `/audit?call_id=…`). Client components only where needed:
  the search playground form, the live-calls poller (M4), confirm dialogs (M4).
- Writes (M4: run campaign, place call, mark task done) are server actions that call the console
  API and `revalidatePath`.
- Errors: `error.tsx` per route segment and an `ApiError` type render an error panel that names the
  failing service; `loading.tsx` shows skeletons.
- UI kit: Tailwind CSS + shadcn/ui primitives (copied into `src/components/ui`), TanStack Table for
  sortable tables, a top banner "Synthetic data · local POC" in the root layout.
- Navigation: **Data explorer** — Overview, Customers, Policies, Test personas, Audit trail;
  **Operations** (M4) — Campaigns, Calls, Follow-ups, Compliance (shown as "coming in M4" until built).

## File layout
```
src/supportagent/console/
  __init__.py
  api.py              create_app(): FastAPI routes, auth dependency, error mapping
  clients.py          BankOpsClient, GatewayServiceClient, SchedulerClient, build_clients()
  views.py            pydantic view models + pure builders (masking happens here)
  (M4) compliance.py  ComplianceSummary aggregation over CallDetail (guardrails + compliance)
scripts/export_console_openapi.py
web/console/
  package.json  package-lock.json  next.config.ts  tsconfig.json  eslint.config.mjs
  vitest.config.ts  playwright.config.ts  components.json  openapi.json
  src/app/            layout.tsx (banner, nav)  page.tsx (overview)  error.tsx  loading.tsx
    customers/page.tsx  customers/[id]/page.tsx
    policies/page.tsx  policies/[docId]/page.tsx  policies/search/page.tsx
    personas/page.tsx  audit/page.tsx
    (M4) campaigns/  calls/  calls/[callId]/  followups/  compliance/
  src/components/     ui/ (shadcn), data-table.tsx, banner.tsx, nav.tsx, error-panel.tsx, …
  src/lib/api/        schema.ts (generated), client.ts (server-only typed fetch)
  src/lib/format.ts   money, dates, flags (pure, unit-tested)
  scripts/            check-types.mjs, check-bundle.mjs, start-standalone.mjs
  tests/unit/         Vitest + React Testing Library
  tests/e2e/          Playwright specs; fake-console-api.mjs replays fixtures/*.json (recorded)
docker/console.Dockerfile   deps → build → runner (Node 24 LTS, output: standalone, non-root)
tests/console/              pytest: clients, views, api routes (MockTransport), openapi export
```

## Data model / contracts
No owned tables. The console API uses `contracts.bank` (`Interaction`, `CallDetail`, `Task`, `Promise`,
`AuditRow`, `Page[T]`), `contracts.kb` and `contracts.scheduler` (`CampaignRunResult`, `ActiveCall`)
upstream, and returns its own view models (`supportagent.console.views`), which are the web app's
contract via OpenAPI. `ComplianceSummary` (M4) has `verified_first_pct`, `disclosures_pct`,
`window_violations`, `cap_violations`, `disputes`, `cease_requests`, `unhonoured`, `failing_call_ids`.

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| UI framework | Next.js (App Router, TS strict) | Project rule: every admin/console UI is Next.js under `web/` (constitution VIII). Replaced Streamlit on 2026-11-07. |
| Backend for the UI | Python console API (FastAPI) | Reuses Python masking, guardrails, compliance and transcripts: one definition of "compliant"; tokens stay server-side; keeps the import-linter boundary |
| Contract | OpenAPI → `openapi-typescript`, committed and drift-checked in CI | Pydantic stays the single source of truth; the UI fails to type-check when it drifts |
| Data fetching | Server components + server actions | No client-side token, no CORS, little client JS |
| UI kit | Tailwind + shadcn/ui, TanStack Table | Accessible primitives owned in-repo, no runtime UI dependency lock-in |
| Package manager | npm with `package-lock.json` | Ships with Node; nothing extra to install |
| Node | 24 LTS in Docker and CI; `engines: >=24` | LTS for images; newer local Node works |
| Port | Console stays on 8501; console API on 8004 | No doc churn; Langfuse keeps 3000 |
| Tests | Vitest + RTL (unit), Playwright against a fake console API (e2e), pytest for the console API | No network, no Python needed for UI tests |

## Dependencies
- Python runtime: `fastapi`, `uvicorn`, `httpx` (already used). `streamlit` is removed by T-CON-14.
- Node (in `web/console/package.json`, exact versions pinned at scaffold time): `next`, `react`,
  `react-dom`, `tailwindcss`, shadcn/ui deps (`class-variance-authority`, `clsx`, `tailwind-merge`,
  `lucide-react`, Radix primitives as needed), `@tanstack/react-table`; dev: `typescript`, `eslint`,
  `eslint-config-next`, `prettier`, `vitest`, `@testing-library/react`, `jsdom`, `@playwright/test`,
  `openapi-typescript`.
- Services (compose):
  - `console-api`: `x-app` image, `uvicorn --factory supportagent.console.api:create_app --port 8004`,
    `ports: ["127.0.0.1:8004:8004"]`, read-only mount `./var:/app/var:ro` for transcripts.
  - `console`: `docker/console.Dockerfile`, `CONSOLE_API_URL=http://console-api:8004`,
    `ports: ["127.0.0.1:8501:8501"]`, depends on `console-api` healthy.
- Settings: `CONSOLE_API_URL` (default `http://localhost:8004`), `CONSOLE_API_TOKEN` (secret). The rest
  (`BANK_API_URL`, `GATEWAY_URL`, `SCHEDULER_URL`, `GATEWAY_SERVICE_TOKEN`) already exist.
- import-linter contract "Console reaches data over HTTP only (CON-R1)" applies to the console API.
- CI: job "Console (Next.js)" — `npm ci`, lint, typecheck, OpenAPI drift check, Vitest, `next build`,
  Playwright (Chromium). The docker job also builds `docker/console.Dockerfile`; release publishes it.
- Make: `console` (compose up `console-api console` and their dependencies), `console-setup`
  (`npm ci` + Playwright Chromium), `console-dev` (`next dev` on :8501 with the console API URL and
  token from the root `.env`), `console-types`, `console-check` (part of `make check`).
- The Streamlit app stays available as compose service `console-streamlit` (profile `legacy`,
  `127.0.0.1:8502`) until T-CON-14 deletes it.
- Dev-only npm audit: 8 moderate advisories in transitive `minimatch` (tooling only, none in runtime
  dependencies, `npm audit --omit=dev` is clean).

## Test strategy
- **unit (pytest):** clients over `MockTransport`; view builders (masking, the 16-digit case); every
  console API route through `TestClient` with faked upstreams, including auth, 502 and 404 mapping;
  the OpenAPI export matches the committed file.
- **unit (Vitest):** `format.ts`; components (data table, error panel, banner); the API client's error
  handling with a mocked `fetch`.
- **e2e (Playwright, CI):** the standalone production server (`scripts/start-standalone.mjs`, as in
  Docker) against `tests/e2e/fake-console-api.mjs`, which replays `tests/e2e/fixtures/*.json`. The
  fixtures are recorded from the real console API over the persona-seeded fakes by
  `tests/console/web_fixtures.py` (`uv run python -m tests.console.web_fixtures`), and a pytest test
  fails when they drift. Checks: each page renders with the banner; Frank's page values; the policy
  search shows `confident`; the audit trail order; the console API down (`POST /__down`) → the error panel.
- **live:** a manual smoke in the demo runbook against the real stack.

## Risks
- Two toolchains in one repo. Mitigation: the Node app is self-contained under `web/console`, with its
  own CI job and Make targets; Python CI is unchanged.
- Upstream API shapes change. The pytest fixtures are built from `contracts` models, and the TS types
  are generated, so a change breaks the tests or the type check loudly.
- Docker build time for the console image. Mitigation: a separate dependency layer keyed on
  `package-lock.json`, so source-only changes rebuild in about a minute.
- Compliance aggregation is slow over many calls. It is limited to the selected range (default today)
  and capped at 200 calls.
