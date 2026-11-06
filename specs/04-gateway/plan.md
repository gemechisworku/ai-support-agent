# 04 · Agent data gateway (MCP) — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md). Design source: [architecture §3](../../docs/architecture.md#3-agent-design).

## Approach
One FastAPI app (`supportagent.gateway.app:app`, port 8002) hosts three surfaces:

1. **Session API** (`/sessions`, worker token). This is the dispatcher side. The agent worker's
   entrypoint code (not the LLM) creates a session from room metadata and holds the returned opaque
   token. The token never enters a prompt.
2. **MCP** (`/mcp`), built with the MCP Python SDK `FastMCP` in streamable-HTTP mode and mounted into
   the FastAPI app. Each tool handler resolves the session from the `Authorization` header
   (`ctx.request_context.request.headers`), then runs one shared pipeline:

   ```
   resolve session → check scope (unverified/verified/any/worker) → rate limit
     → validate input (Pydantic, extra="forbid") → handler (BankClient / Retriever)
     → shape + mask output → audit (fail closed) → ToolResult envelope
   ```
   The pipeline is a single decorator, `@gateway_tool(scope=...)`, in `tools/__init__.py`, so every
   tool gets the same guardrails and tests can call the pipeline without MCP.
3. **Service API** (`/service/*`, service token). Thin REST over the same `BankClient`, for the scheduler.

The gateway is stateless apart from the in-memory `SessionStore`. Bank access goes through
`BankClient` (httpx, async), and policy search goes through an injected `Retriever` (the KB library).
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
  masking.py          # mask_number, mask_phone, assert_no_pii(obj) helper used by tests
  audit.py            # args_hash, AuditWriter (POST /audit via BankClient, fail closed)
  ratelimit.py        # sliding-window limiter keyed by session / credential, injectable clock
  tools/
    __init__.py       # gateway_tool decorator (pipeline), TOOL_REGISTRY
    identity.py       # lookup_by_phone, verify_identity
    account.py        # get_loan_status, get_recent_transactions, get_interaction_history
    writes.py         # record_promise_to_pay, request_callback, escalate, set_contact_flag, record_call_outcome
    policy.py         # search_policy
  mcp_server.py       # FastMCP instance; registers TOOL_REGISTRY with typed signatures
  service.py          # APIRouter for /service/*
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
  phone candidates, and if there are none, the bank identity match by `dob` and `loan_last4`.
  Exactly one match is required; zero or more than one counts as a failed attempt.
- `LoanSummary{loan_masked, product_code, product_name, outstanding_balance, overdue_amount,
  oldest_unpaid_due_date, next_due_date, next_due_amount, days_past_due, fees_accrued, status, speakable}`
  (named `LoanSummary` so it is not confused with the bank's unmasked `contracts.bank.LoanStatus`). `speakable` is, for example,
  "$245.10 due on 12 November 2026". It is built with `Decimal` quantised to 2 dp.
- `TransactionOut{date, amount, channel, status: posted|pending, reference_masked}`.
- **Audit row:** `{ts, call_id, tool, args_hash, customer_id?, decision: allowed|denied:<code>|error, latency_ms}`.

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| Transport | MCP streamable HTTP, mounted in FastAPI | One process and port; reusable by other MCP agents (architecture §6) |
| Error style | `ToolResult` envelope rather than MCP `isError` | Small local models handle one consistent JSON shape better |
| Session store | In memory, one process | POC capacity is 1–2 calls (NFR-CAP); Redis would be a later swap behind the same class |
| Audit | Synchronous, fail closed | Constitution III and NFR-AUDIT: no unaudited data access |
| Outcome write | `record_call_outcome` MCP tool, worker scope, closes the session | One path for all writes; the agent never registers it as an LLM tool |
| Promise rules | Gateway settings until COMP-01 | Keeps M1 independent of M2 |

## Dependencies
Runtime: `mcp` (Python SDK), `fastapi`, `uvicorn`, `httpx`. No extra dev dependency: `HttpBankClient` is
tested with `httpx.MockTransport`.
Compose service `gateway` (uses the `x-app` anchor, `command: uvicorn supportagent.gateway.app:app --host 0.0.0.0 --port 8002`, depends on `bank-api`).
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
- The MCP SDK's header access API may differ between versions. Isolate it in `mcp_server.py` (`_session_token(ctx)`).
- Fuzzy name matching could accept the wrong person. Distance ≤ 1 applies per token **and** DOB and last 4 must match exactly.
