# 04 · Agent data gateway (MCP) — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md). Design source: [architecture §3](../../docs/architecture.md#3-agent-design).

## Approach
One FastAPI app (`supportagent.gateway.app:app`, port 8002) hosts three surfaces:

1. **Session API** (`/sessions`, worker token). This is the dispatcher side. The agent worker's
   entrypoint code (not the LLM) creates a session from room metadata and holds the returned opaque
   token. The token never enters a prompt.
2. **MCP** (`/mcp`), built with the MCP Python SDK 2.x low-level `Server` (`on_list_tools` / `on_call_tool`;
   `FastMCP` is `MCPServer` in 2.x) in stateless streamable-HTTP mode with JSON responses, mounted into the
   FastAPI app; the SDK session manager runs in the app lifespan. Each tool's input schema is its Pydantic
   model (`additionalProperties: false`). The handler reads the session token from the HTTP
   `Authorization` header (`ctx.request.headers`, isolated in `_session_token`), then runs one shared pipeline:

   ```
   resolve session → check scope (unverified/verified/any/worker) → rate limit
     → validate input (Pydantic, extra="forbid") → handler (BankClient / Retriever)
     → shape + mask output → audit (fail closed) → ToolResult envelope
   ```
   Tools register with `@gateway_tool(name, scope=..., input_model=..., output_model=...)` and all run
   through one function, `run_tool(gateway, name, token, args)` in `tools/__init__.py`, so every tool
   gets the same guardrails and tests call the pipeline without MCP. Scope `any` and `worker` tools
   also work on locked sessions (escalation must stay possible); `unverified` tools are refused once
   locked. Validation errors name fields and error types only, never the input values.
3. **Service API** (`/service/*`, service token). Thin REST over the same `BankClient`, for the scheduler.

The gateway is stateless apart from the in-memory `SessionStore`. Bank access goes through
`BankClient` (httpx, async), and policy search goes through an injected `Retriever` (the KB library:
`KbRetriever` runs the synchronous `kb.retrieval.search` in a worker thread; `score` is the chunk's
cosine similarity, or its RRF score when it was a full-text-only hit; `low_confidence = not confident`).
Both are protocols, so unit tests swap in `FakeBank` and `FakeRetriever`.

## File layout
```
src/supportagent/contracts/gateway.py     # all tool I/O models, ToolResult, ToolError, Session*, Candidate, ContactPrefs
src/supportagent/gateway/
  __init__.py
  app.py              # FastAPI app factory: /healthz, /sessions, /service, mount MCP at /mcp
  config.py           # GatewaySettings view over common.settings (tokens, limits, promise rules, salt)
  bank_client.py      # BankClient protocol + HttpBankClient (httpx.AsyncClient, timeouts 2 s, 1 retry on GET)
  session.py          # Session dataclass, SessionStore (TTL, binding, attempt counter, lock, closed)
  identity.py         # name normalisation + matching, verify logic against candidates
  masking.py          # mask_number, mask_phone, speakable_money/date, sanitize_text, assert_no_pii(obj, secrets)
  audit.py            # args_hash, AuditWriter (POST /audit via BankClient, fail closed)
  ratelimit.py        # sliding-window limiter keyed by session / credential, injectable clock
  tools/
    __init__.py       # Gateway (deps), ToolRegistry/REGISTRY + gateway_tool decorator, run_tool (pipeline)
    identity.py       # lookup_by_phone, verify_identity
    account.py        # get_loan_status, get_recent_transactions, get_interaction_history
    writes.py         # record_promise_to_pay, request_callback, escalate, set_contact_flag, record_call_outcome
    policy.py         # search_policy
  retriever.py        # Retriever protocol + KbRetriever (kb.retrieval.search in a worker thread)
  mcp_server.py       # FastMCP instance; registers TOOL_REGISTRY with typed signatures
  service.py          # APIRouter for /service/* scheduler routes (T-GW-10)
  auth.py             # shared service-token check + audit wrapper `service_call` (T-GW-12, reused by T-GW-10)
  kb_browse.py        # /service/kb/* browse + search for the console; KbBrowser protocol, SqlKbBrowser (T-GW-12)
tests/gateway/
  fakes.py            # FakeBank (all 12 personas from bank.seed, LoanStatus via the bank ledger), FakeRetriever
  test_*.py
```

## Data model / contracts
- `Session{call_id, direction, campaign, loan_id?, caller_phone?, expected_customer_id?, candidate_ids[],
  bound_customer_id?, failed_attempts, locked, closed, kb_sources[], created_at, last_seen}`.
  `expected_customer_id` comes from `loan_id` for outbound calls; `candidate_ids` come from
  `GET /customers?phone=` for inbound calls. Neither is ever serialised to a tool output.
- **Name match:** casefold, strip accents and punctuation, then split into tokens. The given-name and
  family-name tokens must each match within Levenshtein distance ≤ 1 (to tolerate STT spelling
  errors). DOB must match exactly. `loan_last4` must equal the last 4 digits of one of the
  candidate's loans.
- **Candidates:** an outbound call checks only `expected_customer_id`. An inbound call checks the
  phone candidates; when the caller's number is not on file, it checks the customers returned by the
  bank's `GET /customers/match?dob=&loan_last4=` for the claim (T-GW-11; a registered number never falls
  back to matching). The name, DOB and last 4 are still checked for every candidate. Exactly one match is required; zero or more than one counts as a failed attempt. A session
  that is already bound returns `verified=true` without re-checking, so it can never be re-bound.
- `LoanSummary{loan_masked, product_code, product_name, outstanding_balance, overdue_amount,
  oldest_unpaid_due_date, next_due_date, next_due_amount, days_past_due, fees_accrued, status, speakable}`
  (named `LoanSummary` so it is not confused with the bank's unmasked `contracts.bank.LoanStatus`). `speakable` is, for example,
  "$245.10 due on 12 November 2026". It is built with `Decimal` quantised to 2 dp.
- `TransactionOut{date, amount, channel, status: posted|pending, reference_masked, speakable}`. Digit runs
  of 5+ in references (which embed the loan number) are masked to their last 4. Without `loan_last4` the
  read tools cover all of the bound customer's loans; transactions are merged newest first, max 5.
- **Audit row** (the bank's `AuditIn`): `{ts, call_id, tool, args_hash, customer_id?, decision: allow|deny|error, latency_ms}`.
  `deny` covers every refusal (scope, validation, rate limit, business rule); `error` is an upstream bank
  failure. Requests without a valid session token are audited with `call_id="unauthenticated"`.
  The per-credential limit (120/min) applies to the worker's session API; per-session limits count
  every attempt, including denied ones.

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| Transport | MCP streamable HTTP, mounted in FastAPI | One process and port; reusable by other MCP agents (architecture §6) |
| Error style | `ToolResult` envelope rather than MCP `isError` | Small local models handle one consistent JSON shape better |
| Session store | In memory, one process | POC capacity is 1–2 calls (NFR-CAP); Redis would be a later swap behind the same class |
| Audit | Synchronous, fail closed | Constitution III and NFR-AUDIT: no unaudited data access |
| Outcome write | `record_call_outcome` MCP tool, worker scope, closes the session | One path for all writes; the agent never registers it as an LLM tool |
| Promise rules | Gateway settings until COMP-01; "today" = the bank's `LoanStatus.as_of`; amount due = overdue amount, else the next instalment | Keeps M1 independent of M2; figures agree with what the agent just read |
| Task owner | bound customer → outbound call's customer → sole phone candidate → none | The bank needs a customer per task; an unknown caller still gets a transfer |
| Outcome guard | `record_call_outcome` rejects another call's id, or a customer/loan the call was not about | The worker cannot attribute an outcome to someone else |

## Dependencies
Runtime: `mcp` (Python SDK), `fastapi`, `uvicorn`, `httpx`. No extra dev dependency: `HttpBankClient` is
tested with `httpx.MockTransport`.
Compose service `gateway` (uses the `x-app` anchor; `alembic -n kb upgrade head` then
`uvicorn --factory supportagent.gateway.app:create_app --host 0.0.0.0 --port 8002`; depends on `bank-api`;
`OLLAMA_URL=http://host.docker.internal:11434` for embeddings). The app is a factory so importing it has no
side effects; `host="0.0.0.0"` disables the SDK's localhost-only Host check because the agent calls
`gateway:8002` on the internal network. M1 exit demo: `scripts/demo_m1.py` (also run by `test_e2e_m1.py`).
Settings: `GATEWAY_WORKER_TOKEN`, `GATEWAY_SERVICE_TOKEN`, `AUDIT_HASH_SALT`, `PROMISE_MAX_DAYS`, `HUMAN_AGENT_AVAILABLE`, `HUMAN_AGENT_EXTENSION`.

## Test strategy
- **unit:** pipeline, session store, identity matching, masking, rate limiter (fake clock), every tool
  handler against `FakeBank` and `FakeRetriever`, MCP tool listing and schemas via an in-process MCP
  client session (`mcp.shared.memory` or the FastAPI `TestClient`), and the service API with `TestClient`.
  `HttpBankClient` is tested with `httpx.MockTransport`.
- **PII guard:** `assert_no_pii(output, seeded_numbers)` runs in a shared parametrised test over every tool response.
- **integration:** `tests/gateway/test_e2e_m1.py` runs the real bank API, the seeded Postgres and the KB
  with the fake embedder, and plays the M1 exit demo: session → verify Frank → loan status → search
  "late fee personal loan" → promise → outcome. It then asserts the audit rows.

## Risks
- The MCP SDK's header access API may differ between versions (it did: 1.x `FastMCP` → 2.x `MCPServer`).
  It is isolated in `mcp_server.py` (`_session_token(ctx)`); `test_mcp_server.py` exercises it over real HTTP.
- Fuzzy name matching could accept the wrong person. Distance ≤ 1 applies per token **and** DOB and last 4 must match exactly.
