# Architecture and infrastructure (POC)

> Agent-readable copy of [design/03-architecture-and-infrastructure.html](design/03-architecture-and-infrastructure.html)
> (5 Nov 2026). The HTML holds the component diagram (Figure 1). If the two disagree, the HTML wins.
> Target host: one Mac (Apple M4, 16 GB). Open source first.

## 1. Architecture at a glance

Three groups, three deliberate boundaries:

```
TELEPHONY & MEDIA            CONVERSATION & MODELS                 DATA, KNOWLEDGE & OPERATIONS
Customer softphone ─┐        Agent worker (LiveKit Agents, Py)     Agent data gateway (MCP)  ◀── security boundary
Human softphone ────┤        VAD → turn detect → STT → LLM → TTS     per-call scoped session
   Asterisk PBX ────┤        State machine: greet·verify·route·      allow-listed tools only
   LiveKit SIP ─────┤          reminder·collections·accountQ·        masking · rate limits · audit
LiveKit Server+Redis┘          policyQ·capture·close                 policy search (hybrid RAG)
                             global: escalate·dispute·cease·3rd    Mock core bank API (FastAPI)
Outreach scheduler           MCP client → gateway tools only       PostgreSQL: db bank, db kb (pgvector)
  compliance gate            Model servers (native, Metal):        Operations console (Streamlit)
  (window, 7-in-7, DNC,        whisper.cpp · llama.cpp Llama 3.1 8B  Langfuse (PII-redacted via Presidio)
   consent) · retries          · nomic-embed · Kokoro-82M · opt. hosted LLM
```

- **Media** (LiveKit) is separate from **reasoning** (agent worker); reasoning is separate from **data** (gateway).
- The agent reaches bank data **only** through gateway tools, and only for the customer verified on that call.
- Contact rules are enforced by the **scheduler before a call exists**, so the model cannot override them.

## 2. Key flows

### Outbound missed-payment call
1. Scheduler asks the gateway (service scope) for accounts 1–30 DPD, filters through the compliance gate:
   local calling window, frequency cap, DNC, consent, open disputes, cease-contact flags.
2. For each eligible account: create a LiveKit room with metadata (`call_id`, `campaign`, `loan_id`),
   call `CreateSIPParticipant` → Asterisk → customer softphone.
3. LiveKit dispatches an agent worker. Worker opens a gateway session bound to `call_id` (unverified).
4. State machine: AI + recording disclosure, right-party check, `verify_identity`. On success the
   gateway binds that one `customer_id` to the session.
5. Overdue amount (`get_loan_status`), reason, consequences (`search_policy`), outcome
   (`record_promise_to_pay` or `request_callback`).
6. Escalation: `escalate` writes a ticket with a summary and SIP-transfers to the human extension.
7. Call end: worker writes the structured outcome, transcript is redacted, traces flushed to Langfuse.

### Inbound call
Customer dials extension **100** → Asterisk → LiveKit SIP → dispatch rule creates a room → agent joins.
Caller number is only a *hint* for candidate customers. General policy questions need no verification;
anything account-specific triggers verification first.

## 3. Agent design

Each state is a LiveKit Agents `Agent` with short instructions and only the tools it needs (≤ 3–4).
Hand-offs are explicit function returns. **Global interrupt intents** (human, dispute, stop calling,
third party, lawyer/bankruptcy) are checked on every turn and override the current state.

| Gateway tool | Scope | Purpose |
|---|---|---|
| `lookup_by_phone` | unverified | Masked first name + candidate ids (held by the gateway, never shown to the model). |
| `verify_identity` | unverified | Checks DOB + last 4 loan digits. Binds the customer on success; locks after 2 failures. |
| `search_policy` | any | Hybrid retrieval; returns chunks with `doc_id`, `version`, `section`. |
| `get_loan_status` | verified | Product, outstanding balance, next due date + amount, DPD, fees accrued. |
| `get_recent_transactions` | verified | Last 5 payments, pending flagged ("I already paid"). |
| `get_interaction_history` | verified | Last calls, open promises, open tickets. |
| `record_promise_to_pay` | verified | Validates date against policy window and amount against what is due. |
| `request_callback` / `escalate` | any | Follow-up task or human hand-off ticket with reason + summary. |
| `set_contact_flag` | verified | Dispute, cease-contact or preferred-time flag. Scheduler honours it immediately. |

**Gateway security rules:** no free-form queries; typed inputs on every tool. Session token comes from
the dispatcher, not the model. Customer ids never accepted from the model after binding. Responses
contain only fields the state needs; full account numbers and national IDs masked. Every call is
rate-limited and audited (who, what, when, result). Tool outputs are data, not instructions
(prompt-injection guard). The agent writes only to interaction tables, never to balances.

## 4. Mock banking system

PostgreSQL + FastAPI internal API. Synthetic data via Faker with a **fixed seed**.

| Table | Key fields | Seed rows |
|---|---|---|
| customers | id, full_name, dob, phone, email, address, timezone, segment, kyc_status, preferred_language | 200 |
| accounts | id, customer_id, type (current/savings), number, balance, status | 260 |
| loan_products | code, name, rate_type, apr_range, term_range, grace_days, late_fee_rule, policy_doc_id | 5 |
| loans | id, customer_id, product_code, number, principal, apr, term_months, start_date, status, days_past_due, bucket | 320 |
| installments | loan_id, seq, due_date, principal_due, interest_due, fee_due, paid_amount, paid_date, status | ~9,000 |
| transactions | id, account_id, loan_id, ts, amount, channel (branch/mobile/transfer), status (posted/pending), reference | ~12,000 |
| contact_preferences | customer_id, consent_voice_ai, dnc, preferred_window, cease_contact, dispute_open | 200 |
| interactions | call_id, customer_id, loan_id, direction, campaign, started_at, outcome JSON (PRD §8), transcript_uri, versions | grows |
| promises_to_pay, tasks | promises (date, amount, kept/broken); callbacks and escalations (type, due, owner, status) | grows |
| audit_log | ts, call_id, tool, args_hash, customer_id, decision, latency_ms | grows |

Loan products: personal loan, auto loan, home mortgage, small-business working capital, salary-advance
microloan — different grace periods and fee rules, so policy answers must be product-specific.

### Scripted personas (~12; phone numbers map to softphone accounts)

| Persona | Situation | Expected outcome |
|---|---|---|
| On-time Olivia | Due in 2 days, never late | Reminder, intent to pay confirmed |
| Forgetful Frank | 5 DPD, first miss | Reason "forgot", promise in 3 days |
| Hardship Hana | 22 DPD, lost job | Hardship referral, escalate to human |
| Disputing Dawit | Claims the fee is wrong | Dispute flag set, collection paused, human |
| Paid-already Priya | Payment pending, not yet posted | Payment in transit, no promise needed |
| Spouse answers | Third party picks up | Neutral message, no details given |
| Stop-calling Sam | Asks never to be called | Cease flag; scheduler excludes him |
| Curious inbound caller | Asks about early repayment | Policy answer with citation |

### Mock core bank API (internal; called only by gateway and scheduler)
`GET /customers/{id}`, `GET /customers?phone=`, `GET /loans/{id}`, `GET /loans/{id}/installments`,
`GET /accounts/{id}/transactions`, `GET /collections/eligible?bucket=1-30`, `POST /interactions`,
`POST /promises`, `POST /tasks`, `PATCH /contact-preferences/{id}`.

## 5. Policy knowledge base

~20 synthetic Markdown policy docs with front-matter (`doc_id`, `title`, `product`, `version`,
`effective_date`, `audience`): product terms (one per product); repayment schedules and due-date rules;
grace periods, late fees, penalty interest; delinquency stages (reminders, fee, bureau reporting at 30
DPD, collections at 60, default at 90); hardship and restructuring (humans decide); promise-to-pay rules
(max 14 days, partial payments); payment channels and posting times; early repayment and payoff;
disputes and error resolution; identity verification and privacy; complaints and escalation; contact
and calling rules (also read by the scheduler as config); required disclosures (versioned script
text); FAQ.

**Ingestion:** split by heading into ~300–500-token chunks with section paths; embed with
`nomic-embed-text` v1.5 (768-d) via llama.cpp `llama-server --embedding`; store in Postgres/pgvector (HNSW) with a `tsvector` column.
**Retrieval:** hybrid vector + full-text merged by reciprocal rank fusion, filtered by product and
effective date, top 4. Below a score threshold → agent offers follow-up instead of guessing.
Re-ingesting a new version keeps the old one for audit.

## 6. Infrastructure

| Component | Choice | Runs as | Why |
|---|---|---|---|
| Container runtime | Docker Desktop / Colima | native | `docker compose up` for Linux services |
| Local PBX | Asterisk | Docker | Softphone extensions, SIP trunk to LiveKit, transfers |
| Softphones | Linphone | Mac/phone | Customer and human-officer ends |
| Media server | LiveKit Server | native (brew) | Rooms, agent dispatch |
| SIP bridge | LiveKit SIP | Docker | SIP ⇄ rooms: inbound dispatch, outbound dial, transfer |
| Message bus | Redis/Valkey | Docker | Required by LiveKit SIP |
| Agent framework | LiveKit Agents (Py), Silero VAD, turn detector | native | Streaming, barge-in, hand-offs, MCP, text-mode testing. Pipecat is fallback. |
| STT | whisper.cpp server (large-v3-turbo / small.en) | native, Metal | Docker on Mac has no GPU |
| LLM + embeddings | llama.cpp `llama-server` (two processes): Llama 3.1 8B Instruct Q4_K_M with tool calling (`--jinja`) on :8090, nomic-embed-text v1.5 Q8_0 (`--embedding`) on :8082 | native, Metal | OpenAI-compatible API (`/v1/chat/completions`, `/v1/embeddings`), plain GGUF files reused across projects, no extra daemon, no data leaves the machine. Replaces Ollama (decided 2026-11-07, disk budget) |
| TTS | Kokoro-82M via Kokoro-FastAPI | native | Streaming, OpenAI-compatible |
| DB + vectors | PostgreSQL 16 + pgvector | Docker | Bank, outcomes and KB in one engine |
| Mock bank API | FastAPI, SQLAlchemy, Faker | native/Docker | Bank system of record |
| Data gateway | MCP Python SDK on FastAPI | native/Docker | Scoped sessions, allow-list, masking, rate limits, audit |
| PII redaction | Microsoft Presidio | library | Redact transcripts/traces |
| Scheduler | Python + APScheduler | native/Docker | Campaigns, compliance gate, pacing, retries, callbacks |
| Console | Streamlit | native/Docker | Trigger calls, outcomes, transcripts, follow-ups |
| Observability | Langfuse self-hosted (Phoenix alt.) | Docker (opt.) | Per-turn traces, latency, eval scores |
| Test harness | pytest + LiveKit Agents test utils | native | Simulated callers, LLM judge |
| Optional hosted LLM | Claude Haiku 5.5 | cloud | Quality/latency benchmark (synthetic data only) |

**Memory budget (~13 GB total):** Llama 3.1 8B Q4_K_M ~5.5 GB with an 8k context (Llama 3.2 3B ~2.5 GB
fallback), whisper small.en ~0.5 GB (large-v3-turbo ~1.6 GB if accuracy needs it), Kokoro + embeddings ~0.8 GB, Docker VM ~1.5 GB, Langfuse
~2.5 GB (start only when needed), Python services ~1 GB.

**Known risks:** SIP/RTP over Docker networking on macOS — publish a fixed RTP port range, pin the
advertised IP; fall back to native Asterisk via Homebrew. LiveKit's browser playground is the
no-telephony fallback from day one.

## 7. POC → production
Carrier SIP trunk + AMD + STIR/SHAKEN; LiveKit cluster/Cloud, autoscaled workers, GPU inference (vLLM)
or managed STT/LLM/TTS (~800 ms); gateway pointed at real core banking behind mTLS/OAuth (agent code
unchanged); jurisdiction-specific rules, legal review, retention, encrypted recordings, sampled QA.
