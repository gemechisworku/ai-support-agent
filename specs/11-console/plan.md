# 11 · Operations console — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md).

## Approach
- One Streamlit multipage app using `st.navigation` with five pages:
  - Campaigns
  - Calls
  - Call detail (reached with a `call_id` query param)
  - Follow-ups
  - Compliance
- Pages are thin. They call `console.clients` and render with `st.dataframe` and `st.metric`. Pure
  formatting and aggregation helpers live in `console.views`, so they are unit-testable without Streamlit.
- **Client injection:** `console.clients.get_clients()` builds `BankOpsClient(settings.bank_api_url)`
  and `SchedulerClient(settings.scheduler_api_url)` once per session (`st.session_state`). Tests
  monkeypatch `get_clients` to return clients backed by `httpx.MockTransport`. Streamlit `AppTest` runs
  the script in-process, so the patch applies.
- **Compliance overview:** fetch the interactions in the date range, then their `CallDetail`, which
  includes audit rows. Run:
  - `observability.guardrails.check_verification_first` and `check_disclosures` (T-EVAL-03)
  - `compliance` `in_calling_window(ts, tz, rules)` and `frequency_cap_ok(attempts, rules)` (T-COMP-02/03)
    over outbound attempts, using `customer_timezone` from `CallDetail`
- Masking: every displayed identifier passes through `views.mask_number()`, a defence in depth even
  though APIs already mask. A top banner reads "Synthetic data · local POC".

## File layout
```
src/supportagent/console/
  __init__.py
  app.py              st.navigation, banner, settings
  clients.py          BankOpsClient, SchedulerClient, get_clients()
  views.py            pure helpers: filters → params, tables, masking, compliance aggregation
  pages/campaigns.py  pages/calls.py  pages/call_detail.py  pages/followups.py  pages/compliance.py
tests/console/
  conftest.py         fake transports + fixture data (interactions, call detail, tasks, run results)
  test_clients.py  test_views.py  test_app_pages.py  test_compliance_view.py
```

## Data model / contracts
No owned tables. It uses `contracts.bank` (`Interaction`, `CallDetail`, `Task`, `Promise`, `AuditRow`,
`Page[T]`) and `contracts.scheduler` (`CampaignRunResult{run_id, eligible, excluded[{loan_id, reasons}]}`,
`ActiveCall{call_id, direction, campaign, started_at, state}`).

`ComplianceSummary` is a local dataclass in `views.py` with these fields:
- `verified_first_pct`
- `disclosures_pct`
- `window_violations`
- `cap_violations`
- `disputes`
- `cease_requests`
- `unhonoured`
- `failing_call_ids`

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| UI | Streamlit | Architecture §6: fastest internal UI; no front-end build |
| Data access | HTTP only | Keeps the console swappable and enforces the boundary |
| Compliance metrics | Reuse `observability.guardrails` + `compliance` | Same rules as the evals, so one definition of "compliant" |
| Refresh | `st.fragment(run_every=5)` on the live table | Live view without reloading the page |
| Tests | `streamlit.testing.v1.AppTest` + `httpx.MockTransport` | Real page execution, no browser, no network |

## Dependencies
- Runtime: `streamlit`, `httpx`, `pandas` (pulled in by Streamlit).
- Service: compose `console` (`x-app`, `command: streamlit run src/supportagent/console/app.py --server.address 0.0.0.0 --server.port 8501`,
  `ports: ["127.0.0.1:8501:8501"]`, read-only mount `./var:/app/var:ro` for transcripts).
- Settings: `SCHEDULER_URL` (default `http://localhost:8003`, added to `.env.example`).

## Test strategy
- **unit:**
  - clients: request paths, params, `PATCH` body and error mapping, over `MockTransport`
  - views: filters, masking, compliance aggregation over fixture details
  - each page through `AppTest`: renders, filters, buttons, the confirm gate, the missing-transcript path
- **integration:** none. Real APIs are exercised during the demo (T-EVAL-06).
- **live:** a manual smoke in the demo runbook.

## Risks
- Upstream API shapes change. The fixtures are built from `contracts` models, so a contract change breaks the tests loudly.
- Compliance aggregation is slow over many calls. It is limited to the selected range (default today) and capped at 200 calls.
