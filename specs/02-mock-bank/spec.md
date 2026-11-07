# 02 · Mock core bank — Spec

> **Code:** `BANK` · **Location:** `src/supportagent/bank/` · **Milestones:** M1, M4
> **PRD:** F5, F7, F13, NFR-ACC, NFR-SEC, D-OUTCOME · **Depends on:** PLAT (settings, DB plumbing, contracts)
> **Status:** see [tasks.md](tasks.md)

## Purpose
A small but realistic stand-in for a bank's core system of record: PostgreSQL holds synthetic customers,
loans, schedules, transactions and contact preferences, and a FastAPI service exposes them as the bank's
**internal API**. It also stores everything a call leaves behind: interactions, promises, tasks, audit
rows and the outbox. Only the gateway and the scheduler call it. The agent never does.

## Scope
**In:** schema and migrations for database `bank`; deterministic seed (random population + scripted
personas); the read and write HTTP API in [architecture.md §4](../../docs/architecture.md); collections
and pre-due eligibility queries; the outbox table for post-call summaries (F13); read endpoints for the
console (M4).
**Out:** identity verification logic, masking and scoping (GW); contact-rule decisions (COMP); policy
text (KB); dialling (SCHED). The bank API is trusted-internal and returns **unmasked** data. Masking is
the gateway's job.

## Requirements
- **BANK-R1** (P0 · F5, D-OUTCOME) — The bank shall persist the tables in architecture §4 (customers, accounts, loan_products, loans, installments, transactions, contact_preferences, interactions, promises_to_pay, tasks, audit_log) plus `outbox`, with Alembic migrations for database `bank`.
- **BANK-R2** (P0 · NFR-ACC, NFR-SEC) — The seed shall be fully synthetic and deterministic: the same seed and `as_of` date produce byte-identical data (≈200 customers, 260 accounts, 5 products, 320 loans, ~9,000 installments, ~12,000 transactions).
- **BANK-R3** (P0 · F5, NFR-ACC) — The seed shall include 12 scripted personas with fixed phone numbers, each placed in exactly the loan situation the persona needs relative to `as_of`.
- **BANK-R4** (P0 · F5) — The bank shall expose read endpoints for customers (by id and by phone), loans, installments, account transactions and a customer's interaction history, returning `supportagent.contracts.bank` models.
- **BANK-R5** (P0 · F5, NFR-ACC) — Loan status responses shall compute outstanding balance, next due date and amount, days past due, bucket and fees accrued from installments in SQL/`Decimal`, never from stored free text.
- **BANK-R6** (P0 · F3, F8) — The bank shall answer eligibility queries: loans 1–30 DPD (`bucket=1-30`) and loans with an instalment due within N days (`due_within=3`), joined with contact preferences and attempt history so the compliance gate can decide.
- **BANK-R7** (P0 · F7, D-OUTCOME, F9) — The bank shall accept writes only to interaction tables: interactions (full PRD §8 outcome), promises, tasks (callback/escalation), contact-preference flags, audit rows. No endpoint mutates balances, loans, installments or transactions.
- **BANK-R8** (P1 · F13) — Writing a final interaction outcome with a summary shall enqueue an `outbox` row (channel `sms` or `email`, rendered text, status `pending`) in the same transaction.
- **BANK-R9** (P0 · F10) — The bank shall expose list/detail read endpoints for interactions, open tasks, promises and the audit log, filterable for the operations console.
- **BANK-R10** (P0 · F4, U3) — The bank shall find customers by date of birth and loan-number last 4 digits (`GET /customers/match?dob=&loan_last4=`), so the gateway can verify inbound callers whose phone number is not on file.
- **BANK-R11** (P0 · F10, F5) — The bank shall expose read-only explorer endpoints: a paginated customer search (`GET /customers?q=&segment=&bucket=&limit=&offset=` returning customer summaries with their loan count and worst bucket), a loan list (`GET /loans?bucket=&product=`), a customer's contact preferences (`GET /customers/{id}/contact-preferences`) and the scripted persona sheet (`GET /personas`).

## Acceptance criteria
- **BANK-R1:** Given an empty `bank` DB, when `alembic upgrade head` runs, then all 12 tables exist with the
  columns in [plan.md](plan.md#data-model--contracts), and `downgrade base` removes them.
- **BANK-R2:** When `python -m supportagent.bank.seed --seed 42 --as-of 2026-11-09` runs twice into fresh
  DBs, then a checksum over all seeded rows is identical, and row counts are within ±5% of the targets.
- **BANK-R3:** Given the seed, then `GET /customers?phone=+15550100302` returns Forgetful Frank, whose
  personal loan is exactly 5 DPD with one missed instalment and no prior misses. The same holds for each
  persona in the plan's table.
- **BANK-R4:** `GET /customers/{id}` for an unknown id → 404. `GET /accounts/{id}/transactions?limit=5`
  returns newest first, and pending rows have `status="pending"`.
- **BANK-R5:** For every seeded loan, `GET /loans/{id}`'s `outstanding_balance` equals
  Σ(principal_due + interest_due + fee_due − paid_amount) of the unpaid instalments, to the cent
  (property test over all loans).
- **BANK-R6:** `GET /collections/eligible?bucket=1-30` returns only loans with 1 ≤ DPD ≤ 30, including
  Frank, Hana and Dawit, and excluding Olivia. `?due_within=3` includes Olivia.
- **BANK-R7:** `POST /promises` creates a promise row. There is no route with a method other than GET on
  `/loans`, `/installments`, `/transactions` or `/accounts` (route-table test).
- **BANK-R8:** `POST /interactions` with `outcome.summary` set creates exactly one `outbox` row atomically.
  If the outbox insert fails, the interaction is rolled back.
- **BANK-R11:** `GET /customers?q=frank` returns a `Page` containing customer 9002; `q=4417` (loan last 4) finds him too;
  `GET /loans?bucket=1-30&product=PERSONAL` only returns such loans; `GET /personas` returns 12 entries with
  situation, expected outcome, extension and verification details; all need the service token.
- **BANK-R10:** `GET /customers/match?dob=1985-03-14&loan_last4=4417` returns exactly Forgetful Frank (a
  `CustomerMatch` with only `customer_id` and `loan_id`); a wrong DOB or last 4 returns `[]`; both parameters are
  required (422 otherwise); the route needs the service token.
- **BANK-R9:** `GET /interactions?campaign=missed_payment&since=…` paginates (`limit`, `offset`).
  `GET /tasks?status=open` lists escalations and callbacks ordered by `due_at`.

## Interfaces
**Provides:** HTTP API on port **8001** (`supportagent.bank.api:app`), authenticated with the header
`X-Service-Token: $GATEWAY_SERVICE_TOKEN`. CLI `python -m supportagent.bank.seed`. Pydantic contracts in
`supportagent/contracts/bank.py`: `Customer`, `Account`, `LoanProduct`, `Loan`, `LoanStatus`,
`Installment`, `Transaction`, `ContactPreferences`, `ContactPreferencesPatch`, `EligibleLoan`,
`InteractionIn`, `Interaction`, `InteractionHistory`, `PromiseIn`, `Promise`, `TaskIn`, `Task`, `AuditIn`,
`OutboxMessage`, `LateFeeRule`, `Page[T]`.
**Consumes:** `supportagent.common.settings` (DB URL, token), `supportagent.common.db`,
`supportagent.contracts.outcome.CallOutcome` (stored as the interaction outcome JSON).

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | `/customers/{id}` | — | `Customer` |
| GET | `/customers?phone=` | E.164 phone | `list[Customer]` (0..n) |
| GET | `/customers/{id}/loans` | — | `list[Loan]` |
| GET | `/loans/{id}` | — | `LoanStatus` |
| GET | `/loans/{id}/installments` | — | `list[Installment]` |
| GET | `/accounts/{id}/transactions` | `limit` (≤50), `loan_id?` | `list[Transaction]` |
| GET | `/customers/{id}/interactions` | `limit` | `InteractionHistory` (interactions + open promises/tasks) |
| GET | `/collections/eligible` | `bucket=1-30` or `due_within=N` | `list[EligibleLoan]` |
| POST | `/interactions` | `InteractionIn` | `Interaction` (upsert by `call_id`) |
| POST | `/promises` | `PromiseIn` | `Promise` |
| POST | `/tasks` | `TaskIn` | `Task` |
| PATCH | `/contact-preferences/{customer_id}` | `ContactPreferencesPatch` | `ContactPreferences` |
| POST | `/audit` | `AuditIn` | 204 |
| GET | `/interactions`, `/interactions/{call_id}`, `/tasks`, `/promises`, `/audit`, `/outbox` | filters + `limit`/`offset` | `Page[...]` (M4) |
| GET | `/healthz` | — | `{"status":"ok"}` (no auth) |

## Non-functional
- Money is `Decimal`/`NUMERIC(12,2)`. Timestamps are `timestamptz` UTC. DOB is `date`.
- p95 < 50 ms per read on seeded data (indexes on phone, customer_id, loan_id, due_date, ts).
- All data is synthetic. Phone numbers use the reserved `+1555…` range only.

## Open questions
- None blocking. Persona situations follow architecture §4. Extra personas (Locked-out Leo, Angry Anton,
  Wrong-number Wendy, Broken-promise Bianca) were added to cover U5, U6 and F12 scenarios.
