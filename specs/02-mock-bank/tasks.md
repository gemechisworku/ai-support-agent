# 02 · Mock core bank — Tasks

> Each task is one GitHub issue and one PR. The field bullets are parsed by `scripts/tasks.py`, so keep
> their exact format. Status is `todo` or `done`. Issue is `—` until `make issues-sync` fills it in.
> Ids are never reused. Order tasks by dependency.

## Tasks

### T-BANK-01 · Bank schema, API contracts and initial migration
- **Status:** done
- **Milestone:** M1
- **Issue:** #6
- **Depends on:** T-PLAT-03, T-PLAT-04
- **Requirements:** BANK-R1

Create the SQLAlchemy models for every table in [plan.md](plan.md#data-model--contracts), the first
Alembic migration for database `bank`, and the Pydantic contracts in `supportagent/contracts/bank.py`.
The gateway (T-GW-01) codes against those contracts, so they land first.

**Acceptance criteria**
- [x] `alembic -n bank upgrade head` (or the PLAT-03 equivalent `make migrate`) creates all 12 tables with indexes on `customers.phone`, `loans.customer_id`, `installments.due_date`, `transactions(account_id, ts)`
- [x] `downgrade base` drops them cleanly
- [x] All contract models from spec §Interfaces exist, use `Decimal` for money and `date`/`datetime` (tz-aware) for time, and round-trip to JSON
- [x] `interactions.outcome` is typed as `CallOutcome` in `InteractionIn`

**Tests**
- `tests/bank/test_models.py` (integration): upgrade → inspect tables/columns/indexes → downgrade
- `tests/contracts/test_bank_contracts.py` (unit): JSON round-trip, Decimal serialised as string with 2 dp, naive datetimes rejected

**Files:** `src/supportagent/bank/models.py`, `src/supportagent/contracts/bank.py`, `migrations/bank/versions/bank_0002_initial_bank_schema.py`, tests above

### T-BANK-02 · Deterministic synthetic population seed
- **Status:** done
- **Milestone:** M1
- **Issue:** #7
- **Depends on:** T-BANK-01
- **Requirements:** BANK-R2, BANK-R5

Implement `ledger.py` (pure derivations) and `seed/population.py` plus the CLI
`python -m supportagent.bank.seed --seed 42 --as-of YYYY-MM-DD [--reset]`. It generates the 5 loan
products (grace days and late-fee rules differ by product), about 200 customers, 260 accounts and 320
loans with amortised instalment schedules, payment history and transactions. Each loan's DPD and bucket
are refreshed from `ledger.py`. Product grace days and late-fee rules must equal the
[KB corpus table](../03-knowledge-base/plan.md#corpus), and `policy_doc_id` is set to the product doc's
`doc_id`. Export them as `PRODUCTS` so the KB cross-check test can read them.

**Acceptance criteria**
- [x] Two runs with the same `--seed`/`--as-of` produce identical row checksums (sha256 over ordered rows)
- [x] Row counts within ±5% of architecture §4 targets
- [x] Bucket distribution includes current, 1-30, 31-60, 61-90 and 90+ loans
- [x] Only `+1555…` phone numbers and `example.com` emails are generated
- [x] Seeding a fresh DB completes in under 10 s locally

**Tests**
- `tests/bank/test_ledger.py` (unit, 200 seeded random schedules): outstanding = Σ unpaid components; DPD = days since the oldest unpaid instalment's due date (due date + 1 day = 1 DPD; grace only decides the late fee); bucket boundaries 0/1/30/31/60/61/90
- `tests/bank/test_seed.py` (integration): determinism checksum, counts, phone/email ranges

**Files:** `src/supportagent/bank/ledger.py`, `src/supportagent/bank/seed/{__init__,__main__,builder,population,products,writer}.py`, tests above

### T-BANK-03 · Scripted persona seed
- **Status:** done
- **Milestone:** M1
- **Issue:** #8
- **Depends on:** T-BANK-02
- **Requirements:** BANK-R3

Overlay the 12 personas from the [plan's table](plan.md#personas-seedpersonaspy-dates-relative-to-as_of)
with fixed ids 9001–9012, fixed phones `+155501003NN` and fixed DOBs and loan numbers. Each one is in its
situation relative to `as_of`. Export `PERSONAS` (name, customer_id, phone, extension, dob,
loan_last4, expected outcome) for evals and telephony provisioning.

**Acceptance criteria**
- [x] Each persona's computed `LoanStatus` matches its situation (e.g. Frank DPD == 5, Olivia next due in 2 days, Priya has a pending transaction equal to amount due, Bianca has an open promise dated `as_of − 1`)
- [x] Persona rows are identical across seeds (only the population varies with `--seed`)
- [x] `PERSONAS` is importable without a DB connection

**Tests**
- `tests/bank/test_personas.py` (integration): one parametrised assertion per persona situation
- `tests/bank/test_personas_unit.py` (unit): ids, phones and extensions are unique; phones are E.164

**Files:** `src/supportagent/bank/seed/personas.py`, tests above

### T-BANK-04 · Read API and bank-api compose service
- **Status:** done
- **Milestone:** M1
- **Issue:** #9
- **Depends on:** T-BANK-01, T-PLAT-05
- **Requirements:** BANK-R4, BANK-R5

FastAPI app `supportagent.bank.api:app` with service-token auth and the GET routes for customers (by id
and phone), customer loans, loan status, installments, account transactions and customer interaction
history. Also add `/healthz` and the `bank-api` service on port 8001 in `docker-compose.yml`.

**Acceptance criteria**
- [x] Missing/wrong `X-Service-Token` → 401 on every route except `/healthz`
- [x] Unknown ids → 404 with `{"detail": …}`; invalid phone → 422
- [x] `GET /loans/{id}` returns `LoanStatus` computed via `ledger.py` for the current `as_of` (settings override `BANK_AS_OF` for tests/demos)
- [x] Transactions are newest first, `limit` capped at 50, and pending are flagged
- [x] `docker compose up bank-api` serves `/healthz` and the image passes the CI docker job

**Tests**
- `tests/bank/test_api_read.py` (integration): each route, 401/404/422 paths, Frank's loan status values, Priya's pending transaction
- `tests/bank/test_ledger_api_consistency.py` (integration): for all seeded loans, API outstanding == ledger outstanding

**Files:** `src/supportagent/bank/api/{__init__,deps,routes_read}.py`, `src/supportagent/bank/queries.py`, `docker-compose.yml`, `tests/bank/conftest.py`, tests above

### T-BANK-05 · Collections and pre-due eligibility query
- **Status:** todo
- **Milestone:** M1
- **Issue:** #10
- **Depends on:** T-BANK-04
- **Requirements:** BANK-R6

Add `GET /collections/eligible?bucket=1-30` and `?due_within=N`. Both return `EligibleLoan` rows joined
with contact preferences and attempt history (`attempts_last_7d`, `last_conversation_at` from
interactions). The bank does **not** filter on contact rules. It supplies the facts and COMP decides.

**Acceptance criteria**
- [ ] `bucket=1-30` returns only loans with 1 ≤ DPD ≤ 30, including Frank, Hana, Dawit and Sam (Sam's `cease_contact` is returned as-is)
- [ ] `due_within=3` returns loans with an unpaid instalment due in [as_of, as_of+3], including Olivia
- [ ] `attempts_last_7d` counts outbound interactions for that loan in the last 7×24 h; `last_conversation_at` is the latest `contact_result=right_party`
- [ ] Exactly one of `bucket`/`due_within` is required (422 otherwise)

**Tests**
- `tests/bank/test_eligibility.py` (integration): persona inclusion/exclusion, attempt counting with inserted interactions, 422 path

**Files:** `src/supportagent/bank/api/routes_collections.py`, `src/supportagent/bank/queries.py`, tests above

### T-BANK-06 · Write API: interactions, promises, tasks, contact preferences, audit, outbox
- **Status:** todo
- **Milestone:** M1
- **Issue:** #11
- **Depends on:** T-BANK-04
- **Requirements:** BANK-R7, BANK-R8

Add `POST /interactions` (upsert by `call_id`; stores the full `CallOutcome` JSON, versions, transcript
and recording URIs), `POST /promises`, `POST /tasks`, `PATCH /contact-preferences/{customer_id}`,
`POST /audit`. When an interaction carries `outcome.summary` and the customer has a phone or email,
enqueue an `outbox` row in the same transaction (F13). Business validation of promises (window,
amount) is the gateway's job. The bank only enforces types and foreign keys.

**Acceptance criteria**
- [ ] Re-posting the same `call_id` updates rather than duplicates
- [ ] PATCH sets only the provided flags (`dnc`, `cease_contact`, `dispute_open`, `preferred_window`, `consent_voice_ai`) and bumps `updated_at`
- [ ] Outbox row created atomically with the interaction; no outbox row when there is no summary
- [ ] Route-table test proves no POST/PUT/PATCH/DELETE exists on accounts, loans, installments or transactions
- [ ] `POST /audit` is idempotent-safe (append-only; returns 204)

**Tests**
- `tests/bank/test_api_write.py` (integration): each write route, upsert, partial PATCH, outbox atomicity (forced failure rolls back)
- `tests/bank/test_routes_readonly_ledger.py` (unit): route table inspection

**Files:** `src/supportagent/bank/api/routes_{interactions,promises,tasks,prefs,audit}.py`, `src/supportagent/bank/queries.py`, tests above

### T-BANK-07 · Operations read endpoints for the console
- **Status:** todo
- **Milestone:** M4
- **Issue:** #66
- **Depends on:** T-BANK-06
- **Requirements:** BANK-R9

Paginated list/detail endpoints the console (T-CON-01) needs: `GET /interactions` (filters:
`direction`, `campaign`, `contact_result`, `escalated`, `since`, `until`), `GET /interactions/{call_id}`
(outcome + promises + tasks + audit rows for that call), `GET /tasks?status=&type=`,
`GET /promises?status=`, `GET /audit?call_id=`, `GET /outbox?status=`. All return `Page[T]`
(`items`, `total`, `limit`, `offset`).

**Acceptance criteria**
- [ ] Filters combine with AND; `limit` defaults to 50 and is capped at 200
- [ ] Tasks ordered by `due_at` ascending; interactions by `started_at` descending
- [ ] Detail for an unknown `call_id` → 404

**Tests**
- `tests/bank/test_api_ops.py` (integration): filters, pagination totals, ordering, call detail aggregation

**Files:** `src/supportagent/bank/api/routes_ops.py`, `src/supportagent/contracts/bank.py` (`Page`, `CallDetail`), tests above
