# 02 · Mock core bank — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md).

## Approach
SQLAlchemy 2.0 declarative models (sync engine, `psycopg` v3) in `bank/models.py`, migrated by the
`bank` Alembic environment that T-PLAT-03 sets up. A FastAPI app (`bank/api/`) has one router per
resource. Routers call small query functions in `bank/queries.py`, which return contract models, so the
SQL is unit-testable without HTTP. Derived loan figures (balance, DPD, bucket, fees) are computed in one
place, `bank/ledger.py`, from installments. `loans.days_past_due` and `bucket` are stored as a
denormalised cache that the seed refreshes, but `LoanStatus` always recomputes them for `as_of`.

The seed is two-phase: `seed/population.py` builds the random population with `Faker` seeded by
`--seed` (default 42) and `random.Random(seed)`, and `seed/personas.py` overlays the 12 personas with
fixed ids ≥ 9001, so persona ids are stable even if population rules change. All dates are derived from
`--as-of` (default: today in UTC). Tests always pass `2026-11-09`.

## File layout
```
src/supportagent/bank/
  models.py            SQLAlchemy models (all tables)
  ledger.py            pure: outstanding, next_due, dpd, bucket, fees_accrued(installments, as_of)
                       DPD = days since the oldest unpaid instalment's due date (due + 1 day = 1 DPD);
                       the grace period only decides when the late fee is added (as_of > due + grace)
  queries.py           read/write functions → contracts.bank models
  api/__init__.py      create_app(); app = create_app()
  api/deps.py          session dependency, service-token auth
  api/routes_read.py   GET customers, loans, installments, transactions, interaction history
  api/routes_*.py      collections (T-BANK-05), writes (T-BANK-06), ops (T-BANK-07)
  seed/__main__.py     CLI: python -m supportagent.bank.seed --seed 42 --as-of 2026-11-09 [--reset]
  seed/products.py     PRODUCTS (= data/policies/product-*.md front matter)
  seed/builder.py      LoanPlan → loan + schedule + payments, placed by its first unpaid instalment
  seed/writer.py       bulk insert, reset, checksum
  seed/population.py   Faker population (fixed per-product and per-bucket quotas, shuffled)
  seed/personas.py     persona definitions (table below)
src/supportagent/contracts/bank.py
migrations/bank/versions/bank_0002_initial_bank_schema.py   (bank_0001 is PLAT's empty baseline)
tests/bank/ test_models.py test_ledger.py test_seed.py test_personas.py test_api_read.py
            test_eligibility.py test_api_write.py test_api_ops.py
```

## Data model / contracts
| Table | Columns (type) |
|---|---|
| customers | id int pk · full_name text · dob date · phone text unique idx · email text · address text · timezone text (IANA) · segment text · kyc_status text · preferred_language text default 'en' |
| accounts | id int pk · customer_id fk · type text(current/savings) · number text(12) · balance numeric(12,2) · status text |
| loan_products | code text pk (`PERSONAL`,`AUTO`,`MORTGAGE`,`SME_WC`,`SALARY_ADV`) · name · rate_type(fixed/variable) · apr_min/apr_max numeric(5,2) · term_min/term_max int · grace_days int · late_fee_rule jsonb (`{"type":"flat"|"pct","value":…,"cap":…}`) · policy_doc_id text |
| loans | id int pk · customer_id fk · product_code fk · number text(10) unique · principal numeric · apr numeric(5,2) · term_months int · start_date date · status(active/closed/default) · days_past_due int · bucket text(`current`,`1-30`,`31-60`,`61-90`,`90+`) · repayment_account_id fk accounts |
| installments | loan_id fk · seq int · due_date date · principal_due · interest_due · fee_due numeric · paid_amount numeric default 0 · paid_date date null · status(scheduled/paid/partial/missed) · pk(loan_id, seq) |
| transactions | id int pk · account_id fk · loan_id fk null · ts timestamptz · amount numeric · channel(branch/mobile/transfer) · status(posted/pending) · reference text |
| contact_preferences | customer_id pk fk · consent_voice_ai bool · dnc bool · preferred_window text null (`"18:00-20:00"`) · cease_contact bool · dispute_open bool · updated_at |
| interactions | call_id uuid pk · customer_id null · loan_id null · direction(inbound/outbound) · campaign text null · started_at · ended_at null · contact_result text · outcome jsonb (CallOutcome) · transcript_uri · recording_uri · versions jsonb |
| promises_to_pay | id pk · call_id fk · loan_id fk · promised_date date · amount numeric · status(open/kept/broken) · created_at |
| tasks | id pk · call_id null · customer_id · loan_id null · type(callback/escalation) · reason text · summary text · due_at timestamptz · owner text null · status(open/done/cancelled) |
| audit_log | id bigserial · ts · call_id · tool text · args_hash text(sha256) · customer_id null · decision(allow/deny/error) · latency_ms int |
| outbox | id pk · call_id fk · channel(sms/email) · recipient text · body text · status(pending/sent) · created_at |

`LoanStatus` = `{loan_id, customer_id, number_last4, product_code, product_name, as_of,
outstanding_balance, overdue_amount, oldest_unpaid_due_date, next_due_date, next_due_amount,
days_past_due, bucket, fees_accrued, status}` (`overdue_amount` and `oldest_unpaid_due_date` give the U2
opener "your payment of [amount] due on [date]"). `EligibleLoan` = `{loan_id,
customer_id, timezone, phone, days_past_due, next_due_date, amount_due, prefs: ContactPreferences,
attempts_last_7d: int, last_conversation_at: datetime|None}` (plus `product_code`, `bucket`). The attempt
figures come from interactions: `attempts_last_7d` counts outbound interactions for the loan since
`clock.now() - 7 days`; `last_conversation_at` is the loan's latest `right_party` interaction. With
`bucket=` the `amount_due` is the overdue amount; with `due_within=N` it is the remaining amount of the
first unpaid instalment due in [as_of, as_of+N] (delinquent loans with such an instalment are included
too; the scheduler decides the campaign). Closed loans are never returned.

### Personas (`seed/personas.py`; dates relative to `as_of`)
| id | Persona | Phone (ext) | Product | Situation |
|---|---|---|---|---|
| 9001 | On-time Olivia | +15550100301 (301) | PERSONAL | next instalment due in 2 days, never late |
| 9002 | Forgetful Frank | +15550100302 (302) | PERSONAL | 5 DPD, first ever miss |
| 9003 | Hardship Hana | +15550100303 (303) | AUTO | 22 DPD, two partials before |
| 9004 | Disputing Dawit | +15550100304 (304) | SME_WC | 12 DPD; fee_due includes a late fee he disputes |
| 9005 | Paid-already Priya | +15550100305 (305) | PERSONAL | 3 DPD, a pending transfer equal to the amount due |
| 9006 | Marcus Bell (spouse answers) | +15550100306 (306) | MORTGAGE | 8 DPD; scenario has a third party answering |
| 9007 | Stop-calling Sam | +15550100307 (307) | PERSONAL | 15 DPD |
| 9008 | Curious Cleo (inbound) | +15550100308 (308) | MORTGAGE | current |
| 9009 | Locked-out Leo | +15550100309 (309) | AUTO | 6 DPD (fails verification twice in the scenario) |
| 9010 | Angry Anton | +15550100310 (310) | SALARY_ADV | 9 DPD |
| 9011 | Wrong-number Wendy | +15550100311 (311) | PERSONAL | 4 DPD; the number now belongs to someone else |
| 9012 | Broken-promise Bianca | +15550100312 (312) | PERSONAL | 10 DPD, open promise dated `as_of − 1` (missed) |

All personas have `consent_voice_ai=true`, `dnc=false`, no contact flags set and timezone
`America/New_York`. Full names, DOBs (e.g. Frank Fischer 1985-03-14) and loan numbers (`552026` + last 4,
e.g. Frank `5520264417`) are fixed in `personas.py`; `PERSONAS` / `PERSONA_BY_ID` (pure data:
`customer_id`, `name`, `first_name`, `phone`, `extension`, `dob`, `loan_id` = customer id, `loan_number`,
`loan_last4`, `days_past_due`, `next_due_in_days`, `situation`, `expected_outcome`) are what T-EVAL and
T-TEL read. Persona rows come from a fixed RNG, so they never change with `--seed`; the population is
sized 188/248/308 so totals stay 200 customers / 260 accounts / 320 loans. Hana's "two partials" are two
earlier instalments each paid in two transactions. Bianca also has the earlier outbound call (8 days
before `as_of`, outside the 7-day cool-off) that produced her open promise.

`call_id` on `promises_to_pay`, `tasks` and `audit_log` is a plain column, not a foreign key: the
gateway writes them mid-call, before the interaction row exists. `outbox.call_id` is a foreign key
(written together with the interaction). `GET /customers/{id}/interactions` returns
`InteractionHistory{interactions, open_promises, open_tasks}`.

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| Sync vs async SQLAlchemy | sync + FastAPI threadpool | simpler; load is 1–2 calls |
| Derived figures | computed in `ledger.py` per request | single source of truth for NFR-ACC |
| Auth | static service token header | internal API; mTLS later (architecture §7) |
| Interaction write | upsert by `call_id`; first `started_at` wins; nullable fields (outcome, ended_at, URIs, versions, ids, campaign) keep their stored value unless the new write sets them; `contact_result` is overwritten | scheduler creates attempt row, worker completes it |
| Outbox (F13) | one row per call, written in the same savepoint as the interaction when `outcome.summary` is non-empty; SMS to the customer's phone (email if none); body `"<BANK_NAME>: <summary> …"` | atomic; re-posts never duplicate |
| Write errors | unknown loan/customer/preferences → 404; schema errors → 422; promises/tasks/audit `call_id` not checked | gateway validates business rules |

## Dependencies
`fastapi`, `uvicorn[standard]`, `sqlalchemy>=2`, `psycopg[binary]>=3`, `alembic`, `faker`. Dev:
`httpx` (TestClient). Ledger property tests use seeded `random` loops instead of `hypothesis` (no extra
dependency). Compose service `bank-api` (the `x-app`
anchor; runs `alembic -n bank upgrade head`, then `uvicorn supportagent.bank.api:app` on port 8001;
required secrets default to the `.env.example` placeholders; healthcheck on `/healthz`). Seed it with
`docker compose exec bank-api python -m supportagent.bank.seed --as-of 2026-11-09 --reset`.

## Test strategy
- **unit:** `ledger.py` (200 seeded random schedules), persona definitions (pure), route table
  (no mutating routes on ledger resources), seed determinism of the in-memory generator.
- **integration:** migrations up/down; seed → counts and checksum; every endpoint through FastAPI
  `TestClient` against `bank_test`, using the T-PLAT-05 per-test transaction fixture. `tests/bank/conftest.py`
  seeds `bank_test` once per module (`seeded_bank`, committed) and gives an `api` client bound to the
  rolled-back `bank_db` session with the clock frozen at 2026-11-09.

## Risks
- Seed runtime: bulk insert with `executemany`/COPY; target < 10 s.
- Date-relative personas drift with real time. Always pass `--as-of` in tests and demos.
