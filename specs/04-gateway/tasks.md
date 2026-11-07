# 04 · Agent data gateway (MCP) — Tasks

> Each task is one GitHub issue and one PR. The field bullets are parsed by `scripts/tasks.py`, so keep
> their exact format. Status is `todo` or `done`. Issue is `—` until `make issues-sync` fills it in.

## Tasks

### T-GW-01 · Tool contracts and bank API client
- **Status:** done
- **Milestone:** M1
- **Issue:** #12
- **Depends on:** T-PLAT-04, T-BANK-01
- **Requirements:** GW-R1, GW-R10

Define every gateway model in `contracts/gateway.py`: tool inputs and outputs, `ToolResult[T]`,
`ToolError`, `SessionCreate`/`SessionCreated`, `Candidate`, `ContactPrefs`, `CallAttempt` and
`EscalationReason`, with `extra="forbid"`. Implement the `BankClient` protocol and an `HttpBankClient`
over the bank API DTOs from `contracts/bank.py`. See [plan.md](plan.md#data-model--contracts).

**Acceptance criteria**
- [x] All models in the spec's tool table exist; no input model has a `customer_id` field
- [x] Money fields are `Decimal`, and dates are `date`/`datetime` (timezone-aware)
- [x] `HttpBankClient` covers every bank endpoint the gateway consumes, with 2 s timeouts and one retry on GET only
- [x] `tests/gateway/fakes.py` provides `FakeBank` implementing `BankClient`, seeded with the personas Frank, Hana, Priya, Olivia and Sam

**Tests**
- `tests/contracts/test_gateway_contracts.py` (unit): extra fields rejected; `loan_last4` pattern `^\d{4}$`; `ToolResult` round-trips
- `tests/gateway/test_bank_client.py` (unit, `httpx.MockTransport`): URL and params per method; DTO parsing; GET retry; no retry on POST

**Files:** `src/supportagent/contracts/gateway.py`, `src/supportagent/gateway/bank_client.py`, `tests/gateway/fakes.py`, tests above

### T-GW-02 · Per-call session manager and identity binding
- **Status:** done
- **Milestone:** M1
- **Issue:** #13
- **Depends on:** T-GW-01
- **Requirements:** GW-R2

Build `SessionStore` and the `POST /sessions` / `DELETE /sessions/{call_id}` routes, with
worker-token auth. Sessions issue opaque random tokens (`secrets.token_urlsafe(32)`) and have a TTL
(30 min idle, 60 min max). Outbound sessions resolve `expected_customer_id` from `loan_id`; inbound
sessions resolve candidates from `caller_phone`. `bind(customer_id)` is callable only from
`identity.py`. Add the `@gateway_tool(scope=…)` pipeline skeleton: resolve session → scope → validate → handler → envelope.

**Acceptance criteria**
- [x] Missing, unknown or expired token → `forbidden`; a closed session → `session_closed`
- [x] Scopes are enforced by the pipeline: `verified` tools return `not_verified` before binding, and `locked` after a lock
- [x] Creating a second session for the same `call_id` returns the existing token (idempotent)
- [x] A session's ids and token never appear in any tool output

**Tests**
- `tests/gateway/test_session.py` (unit): TTL expiry with fake clock; idempotent create; close; wrong worker token → 401
- `tests/gateway/test_pipeline.py` (unit): scope matrix (unverified/verified/any/worker × unbound/bound/locked/closed)

**Files:** `src/supportagent/gateway/session.py`, `src/supportagent/gateway/tools/__init__.py`, `src/supportagent/gateway/app.py`, `src/supportagent/gateway/config.py`

### T-GW-03 · Field masking and response shaping
- **Status:** done
- **Milestone:** M1
- **Issue:** #14
- **Depends on:** T-GW-01
- **Requirements:** GW-R5, GW-R10

Write `mask_number` (`****1234`), `mask_phone` (`***-***-1234`) and `speakable_money` / `speakable_date`
(Decimal to 2 dp half-up, "$1,245.10", "12 November 2026"). Add `sanitize_text` for policy chunks: strip control and
zero-width characters, truncate to 2,000 characters. Add the `assert_no_pii(obj, secrets)` test
helper. All tool outputs are built from explicit field lists; bank DTOs are never passed through.

**Acceptance criteria**
- [x] No output model contains surname, full DOB, address, email or national id fields
- [x] `assert_no_pii` flags any run of 5 or more consecutive digits of a seeded number

**Tests**
- `tests/gateway/test_masking.py` (unit): masking edge cases (short numbers, None); Decimal rounding (half-up); `sanitize_text` strips `​` and ANSI codes and truncates

**Files:** `src/supportagent/gateway/masking.py`, `tests/gateway/test_masking.py`

### T-GW-04 · Audit logging and rate limiting
- **Status:** done
- **Milestone:** M1
- **Issue:** #15
- **Depends on:** T-GW-02
- **Requirements:** GW-R8, GW-R9

Add an `AuditWriter` (`POST /audit` through `BankClient`) and a sliding-window `RateLimiter`, and wire
both into the pipeline. Limits: per session 20/min and 150/call, `search_policy` 10/min, per
credential 120/min. `args_hash = sha256(salt + canonical_json(args))`. A denied call is audited too.
If the audit write fails, return `audit_unavailable` and no data.

**Acceptance criteria**
- [x] One audit row per request with `call_id, tool, args_hash, customer_id, decision, latency_ms`
- [x] Raw argument values never appear in an audit row or a log line
- [x] The limits trigger exactly at the documented thresholds

**Tests**
- `tests/gateway/test_audit.py` (unit): row fields; hash stable and salted; fail-closed on a 500 from FakeBank
- `tests/gateway/test_ratelimit.py` (unit, fake clock): 21st call/min denied; 151st call/session denied; window slides

**Files:** `src/supportagent/gateway/audit.py`, `src/supportagent/gateway/ratelimit.py`, pipeline in `tools/__init__.py`

### T-GW-05 · Identity tools: lookup_by_phone and verify_identity
- **Status:** done
- **Milestone:** M1
- **Issue:** #16
- **Depends on:** T-GW-02, T-GW-03
- **Requirements:** GW-R3, GW-R2

Implement the two unverified tools. `lookup_by_phone` uses the session's phone (it takes no
arguments) and returns `first_name` only for outbound sessions. `verify_identity` normalises and
fuzzy-matches the name (≤ 1 edit per token), matches DOB and loan last 4 exactly, binds on a single
match, and locks after 2 failures. See [plan.md](plan.md#data-model--contracts).

**Acceptance criteria**
- [x] Outbound: only `expected_customer_id` can be bound
- [x] Inbound: the phone's candidates; more than one match counts as a failure. (The bank-side identity
  match for unknown numbers is not built: the bank API has no identity-search endpoint — see the spec's
  open questions. Until then an unknown number's attempt counts as a failure, tested.)
- [x] `attempts_remaining` counts down 2 → 1 → 0 and `locked=true` at 0; once locked, further `verify_identity` calls return `locked`
- [x] The response never says *which* factor failed

**Tests**
- `tests/gateway/test_identity.py` (unit): name normalisation ("José  O'Neil" vs "jose oneil"); distance-1 tolerance; distance-2 rejected
- `tests/gateway/test_identity_tools.py` (unit): Frank outbound success binds; wrong DOB twice locks; inbound unknown number cannot verify yet (counts as a failure); spouse (third party) cannot bind; inbound lookup returns no name

**Files:** `src/supportagent/gateway/identity.py`, `src/supportagent/gateway/tools/identity.py`

### T-GW-06 · Verified read tools: loan status, transactions, interaction history
- **Status:** done
- **Milestone:** M1
- **Issue:** #17
- **Depends on:** T-GW-05
- **Requirements:** GW-R4, GW-R5

Implement `get_loan_status`, `get_recent_transactions` (last 5, pending flagged) and
`get_interaction_history` (structured fields only: dates, campaigns, contact results, promise
date/amount/status, task type/due/status, with no free-text notes). All three are scoped to the bound
customer; an optional `loan_last4` picks among that customer's loans only.

**Acceptance criteria**
- [x] Values equal FakeBank values exactly; `speakable` strings match the values
- [x] A `loan_last4` that is not owned by the bound customer → `not_found` (no information about other customers' loans leaks)
- [x] Priya's pending payment appears with `status="pending"`

**Tests**
- `tests/gateway/test_read_tools.py` (unit): exact values for Frank, Hana and Priya; unverified → `not_verified`; foreign last4 → `not_found`; parametrised `assert_no_pii` over all outputs

**Files:** `src/supportagent/gateway/tools/account.py`, `tests/gateway/test_read_tools.py`

### T-GW-07 · Write tools: promise, callback/escalate, contact flag, call outcome
- **Status:** done
- **Milestone:** M1
- **Issue:** #18
- **Depends on:** T-GW-05
- **Requirements:** GW-R6, GW-R10

Implement `record_promise_to_pay` (window today..today+`promise_max_days`; 0 < amount ≤ amount due,
partial allowed per settings), `request_callback`, `escalate` (creates a task; returns
`human_available` and `transfer_extension` from settings), `set_contact_flag` (dispute / cease_contact
/ preferred_window via `PATCH /contact-preferences`), and the worker-scope `record_call_outcome`
(validates `CallOutcome`, adds the session's `kb_sources`, posts `/interactions`, closes the session).

**Acceptance criteria**
- [x] The promise reasons `ok | date_out_of_window | amount_invalid | loan_not_found` are returned with `max_date` and `amount_due`
- [x] No write targets balances, loans, installments or transactions (the `FakeBank` call log is asserted)
- [x] After `record_call_outcome`, every tool returns `session_closed`; a second outcome write is rejected
- [x] `escalate` works on unverified and locked sessions. The bank requires a customer on every task, so
  the task goes to the bound customer, else the customer the call is about (outbound) or the caller's only
  registered customer; with none, `escalate` returns the transfer target with `task_id=null` (no ticket)

**Tests**
- `tests/gateway/test_write_tools.py` (unit): promise boundary dates (today, +14, +15); amounts (0, due, due+0.01); dispute flag sets `dispute_open`; cease flag; outcome closes the session; kb_sources merged into the outcome

**Files:** `src/supportagent/gateway/tools/writes.py`, `tests/gateway/test_write_tools.py`

### T-GW-08 · search_policy tool
- **Status:** done
- **Milestone:** M1
- **Issue:** #19
- **Depends on:** T-GW-02, T-KB-06
- **Requirements:** GW-R7, GW-R10

Wrap the KB retrieval function behind a `Retriever` protocol (injected, so tests use
`FakeRetriever`). Map the results to `PolicySource{doc_id, version, section, text, score}`,
sanitise the text, set `low_confidence` from the KB threshold, and append the sources to
`session.kb_sources`. The product filter is optional. When verified with a single loan, it defaults
to that loan's product.

**Acceptance criteria**
- [x] Returns at most 4 chunks; `low_confidence` matches the threshold rule
- [x] Callable unverified (U4 needs no verification)
- [x] An injection string inside a chunk is returned as plain data and does not alter the session

**Tests**
- `tests/gateway/test_search_policy.py` (unit): FakeRetriever high/low scores; product defaulting; sources recorded; injection chunk unchanged scope
- `tests/gateway/test_search_policy_kb.py` (integration): real KB with fake embedder, "late fee personal loan" returns the late-fee doc

**Files:** `src/supportagent/gateway/tools/policy.py`, tests above

### T-GW-09 · MCP server, compose service and M1 end-to-end test
- **Status:** done
- **Milestone:** M1
- **Issue:** #20
- **Depends on:** T-GW-04, T-GW-06, T-GW-07, T-GW-08, T-BANK-06
- **Requirements:** GW-R1, GW-R8

Register every tool with the MCP SDK (streamable HTTP) mounted at `/mcp` and read the bearer token from
request headers. Add the `gateway` compose service on 8002. Add `scripts/demo_m1.py` (the milestone
exit demo) and an integration test that does the same through a real MCP client.

**Acceptance criteria**
- [x] `tools/list` returns exactly the 11 tools in the spec, with strict schemas
- [x] `docker compose up gateway` is healthy; `/healthz` ok (verified with the runtime image and the
  compose command; the compose file validates)
- [x] The M1 exit demo runs green, and the audit log shows every call with the right `call_id`

**Tests**
- `tests/gateway/test_mcp_server.py` (unit): in-process MCP client lists tools; calls `search_policy` and `verify_identity` with a header token; a missing header → `forbidden` envelope
- `tests/gateway/test_e2e_m1.py` (integration): bank API + seeded DB + KB + gateway; Frank flow end to end; audit row count equals the call count

**Files:** `src/supportagent/gateway/mcp_server.py`, `src/supportagent/gateway/app.py`, `docker-compose.yml` (gateway service), `scripts/demo_m1.py`, tests above

### T-GW-10 · Service-scope eligibility endpoint for the scheduler
- **Status:** todo
- **Milestone:** M3
- **Issue:** #53
- **Depends on:** T-GW-09, T-BANK-05
- **Requirements:** GW-R11, GW-R8

Add the `/service` router with service-token auth: `GET /service/candidates?campaign=` (pre_due =
instalment due within 3 days, missed_payment = 1–30 DPD, callback = due callback tasks and broken
promises), `GET /service/contact-preferences/{customer_id}` and `POST /service/attempts`. Every
request is audited with `call_id` (or `service` when there is no call).

**Acceptance criteria**
- [ ] The candidate payload carries every field the compliance gate needs (contact prefs, timezone, attempts_last_7d, last_conversation_at)
- [ ] Worker and session tokens → 403; the service token cannot call MCP tools
- [ ] An attempt with `contact_result` in the PRD §8 enum creates an interaction row

**Tests**
- `tests/gateway/test_service_api.py` (unit, FakeBank): each campaign filter; auth matrix; attempt validation
- `tests/gateway/test_service_api_db.py` (integration): Frank appears in missed_payment and Olivia in pre_due; Sam is still listed with `cease_contact=true` (filtering is the scheduler's job)

**Files:** `src/supportagent/gateway/service.py`, tests above

### T-GW-11 · Verify inbound callers from unregistered numbers
- **Status:** done
- **Milestone:** M2
- **Issue:** #94
- **Depends on:** T-GW-05, T-BANK-08
- **Requirements:** GW-R3

Closes the open question in spec.md: add `BankClient.match_customers(dob, loan_last4)` and, in
`identity.verify` for inbound sessions, add those matches to the candidates when the caller's phone
has none. The 2-failure lock, the name check and the "never reveal why it failed" rule stay as they are.

**Acceptance criteria**
- [x] An inbound session from an unknown number verifies Frank with his name, DOB and loan last 4
- [x] A wrong DOB from an unknown number still counts as a failed attempt, and two lock the session
- [x] A DOB + last-4 match with a different name does not verify (the name check still applies)

**Tests**
- `tests/gateway/test_identity.py` (unit, FakeBank): the three cases above
- `tests/gateway/test_identity_db.py` (integration): unknown-number inbound verification against the seeded bank

**Files:** `src/supportagent/gateway/bank_client.py`, `src/supportagent/gateway/identity.py`, `specs/04-gateway/spec.md` (remove the open question), tests above

### T-GW-12 · Knowledge-base browse endpoints for the console
- **Status:** done
- **Milestone:** M2
- **Issue:** #99
- **Depends on:** T-GW-09
- **Requirements:** GW-R12

Add the read-only `/service/kb/*` routes listed under Interfaces, authenticated by the service token
(create the shared service-token dependency here; T-GW-10 reuses it). `search` calls
`kb.retrieval.search` with the gateway's configured embedder and threshold, so the console shows
exactly what the agent would get. Every request writes an audit row with `call_id="service"`.

**Acceptance criteria**
- [x] The GW-R12 acceptance criterion in spec.md holds
- [x] Contracts `KbDocumentSummary` and `KbDocumentDetail` live in `contracts/kb.py`
- [x] The routes read only the `kb` database; no bank call is made

**Tests**
- `tests/gateway/test_kb_browse.py` (unit with a fake KB reader): routes, auth matrix, audit rows
- `tests/gateway/test_kb_browse_db.py` (integration): against an ingested `kb_test` with HashEmbedder; search equals `search_policy`

**Files:** `src/supportagent/gateway/kb_browse.py`, `src/supportagent/gateway/app.py`, `src/supportagent/contracts/kb.py`, tests above
