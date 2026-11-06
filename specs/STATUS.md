# Status

<!-- GENERATED on main by .github/workflows/status.yml after each merge. Do not edit. -->

**Overall:** █░░░░░░░░░░░░░░░░░░░ 3/80 tasks done

## By milestone

| Milestone | Due | Progress |
|---|---|---|
| M0 · Foundation | 2026-11-10 | █████░░░░░ 3/6 |
| M1 · Data and knowledge | 2026-11-13 | ░░░░░░░░░░ 0/21 |
| M2 · Conversation | 2026-11-20 | ░░░░░░░░░░ 0/19 |
| M3 · Telephony and outbound | 2026-11-27 | ░░░░░░░░░░ 0/20 |
| M4 · Operations, quality and demo | 2026-12-04 | ░░░░░░░░░░ 0/14 |

## By module

| Module | Progress |
|---|---|
| [01-platform](01-platform/tasks.md) | █████░░░░░ 3/6 |
| [02-mock-bank](02-mock-bank/tasks.md) | ░░░░░░░░░░ 0/7 |
| [03-knowledge-base](03-knowledge-base/tasks.md) | ░░░░░░░░░░ 0/6 |
| [04-gateway](04-gateway/tasks.md) | ░░░░░░░░░░ 0/10 |
| [05-compliance](05-compliance/tasks.md) | ░░░░░░░░░░ 0/5 |
| [06-agent](06-agent/tasks.md) | ░░░░░░░░░░ 0/12 |
| [07-voice](07-voice/tasks.md) | ░░░░░░░░░░ 0/6 |
| [08-telephony](08-telephony/tasks.md) | ░░░░░░░░░░ 0/6 |
| [09-scheduler](09-scheduler/tasks.md) | ░░░░░░░░░░ 0/6 |
| [10-observability](10-observability/tasks.md) | ░░░░░░░░░░ 0/5 |
| [11-console](11-console/tasks.md) | ░░░░░░░░░░ 0/5 |
| [12-evals](12-evals/tasks.md) | ░░░░░░░░░░ 0/6 |

## Tasks

| | Task | Title | Milestone | Depends on | Issue |
|---|---|---|---|---|---|
| ✅ | T-PLAT-01 | Repository bootstrap: toolchain, Docker, CI/CD, spec workflow | M0 | — | #80 |
| ✅ | T-PLAT-02 | Typed settings and PII-safe structured logging | M0 | T-PLAT-01 | #1 |
| ⬜ | T-PLAT-03 | Database plumbing and Alembic for bank and kb | M0 | T-PLAT-02 | #2 |
| ✅ | T-PLAT-04 | Shared contracts: CallOutcome and common enums | M0 | T-PLAT-01 | #3 |
| ⬜ | T-PLAT-05 | Test infrastructure: DB fixtures, frozen clock, factories | M0 | T-PLAT-03 | #4 |
| ⬜ | T-PLAT-06 | make doctor and native dev-setup guide | M0 | T-PLAT-01 | #5 |
| ⬜ | T-BANK-01 | Bank schema, API contracts and initial migration | M1 | T-PLAT-03, T-PLAT-04 | #6 |
| ⬜ | T-BANK-02 | Deterministic synthetic population seed | M1 | T-BANK-01 | #7 |
| ⬜ | T-BANK-03 | Scripted persona seed | M1 | T-BANK-02 | #8 |
| ⬜ | T-BANK-04 | Read API and bank-api compose service | M1 | T-BANK-01, T-PLAT-05 | #9 |
| ⬜ | T-BANK-05 | Collections and pre-due eligibility query | M1 | T-BANK-04 | #10 |
| ⬜ | T-BANK-06 | Write API: interactions, promises, tasks, contact preferences, audit, outbox | M1 | T-BANK-04 | #11 |
| ⬜ | T-BANK-07 | Operations read endpoints for the console | M4 | T-BANK-06 | #66 |
| ⬜ | T-KB-01 | Synthetic policy corpus | M1 | T-PLAT-01 | #21 |
| ⬜ | T-KB-02 | KB schema and migration (documents, chunks, HNSW, tsvector) | M1 | T-PLAT-03 | #22 |
| ⬜ | T-KB-03 | Heading-based markdown chunker | M1 | T-PLAT-01 | #23 |
| ⬜ | T-KB-04 | Embedder interface: Ollama and deterministic fake | M1 | T-PLAT-02 | #24 |
| ⬜ | T-KB-05 | Versioned ingestion CLI | M1 | T-KB-01, T-KB-02, T-KB-03, T-KB-04 | #25 |
| ⬜ | T-KB-06 | Hybrid retrieval (RRF, filters, threshold) with golden questions | M1 | T-KB-05 | #26 |
| ⬜ | T-GW-01 | Tool contracts and bank API client | M1 | T-PLAT-04, T-BANK-01 | #12 |
| ⬜ | T-GW-02 | Per-call session manager and identity binding | M1 | T-GW-01 | #13 |
| ⬜ | T-GW-03 | Field masking and response shaping | M1 | T-GW-01 | #14 |
| ⬜ | T-GW-04 | Audit logging and rate limiting | M1 | T-GW-02 | #15 |
| ⬜ | T-GW-05 | Identity tools: lookup_by_phone and verify_identity | M1 | T-GW-02, T-GW-03 | #16 |
| ⬜ | T-GW-06 | Verified read tools: loan status, transactions, interaction history | M1 | T-GW-05 | #17 |
| ⬜ | T-GW-07 | Write tools: promise, callback/escalate, contact flag, call outcome | M1 | T-GW-05 | #18 |
| ⬜ | T-GW-08 | search_policy tool | M1 | T-GW-02, T-KB-06 | #19 |
| ⬜ | T-GW-09 | MCP server, compose service and M1 end-to-end test | M1 | T-GW-04, T-GW-06, T-GW-07, T-GW-08, T-BANK-06 | #20 |
| ⬜ | T-GW-10 | Service-scope eligibility endpoint for the scheduler | M3 | T-GW-09, T-BANK-05 | #53 |
| ⬜ | T-COMP-01 | Versioned contact-rules config and loader | M2 | T-PLAT-04 | #35 |
| ⬜ | T-COMP-02 | Calling-window check in customer local time | M3 | T-COMP-01 | #50 |
| ⬜ | T-COMP-03 | Frequency cap (7-in-7 and post-conversation cool-off) | M3 | T-COMP-01 | #51 |
| ⬜ | T-COMP-04 | Eligibility gate: DNC, consent, dispute, cease-contact | M3 | T-COMP-02, T-COMP-03 | #52 |
| ⬜ | T-COMP-05 | Disclosure registry and required-disclosure checker | M2 | T-COMP-01 | #36 |
| ⬜ | T-AGENT-01 | Worker skeleton: call context and gateway MCP client | M2 | T-GW-01, T-VOICE-02 | #27 |
| ⬜ | T-AGENT-02 | State machine framework and versioned prompt registry | M2 | T-AGENT-01, T-OBS-02 | #28 |
| ⬜ | T-AGENT-03 | Global interrupt intents | M2 | T-AGENT-02 | #29 |
| ⬜ | T-AGENT-04 | Greeting, disclosure and identity verification states | M2 | T-AGENT-02, T-COMP-05 | #30 |
| ⬜ | T-AGENT-05 | Inbound routing and intent detection | M2 | T-AGENT-04 | #31 |
| ⬜ | T-AGENT-06 | Account questions (U3) | M2 | T-AGENT-05 | #32 |
| ⬜ | T-AGENT-07 | Policy questions with citations and low-confidence fallback (U4) | M2 | T-AGENT-05 | #33 |
| ⬜ | T-AGENT-08 | Outcome capture, close and post-call summary | M2 | T-AGENT-04, T-OBS-03 | #34 |
| ⬜ | T-AGENT-09 | Pre-due reminder flow (U1) | M3 | T-AGENT-08 | #46 |
| ⬜ | T-AGENT-10 | Missed-payment collections flow (U2) | M3 | T-AGENT-07, T-AGENT-08 | #47 |
| ⬜ | T-AGENT-11 | Escalation and warm transfer (U6) | M3 | T-AGENT-03, T-AGENT-08, T-TEL-04 | #48 |
| ⬜ | T-AGENT-12 | Callback flow with context (U5) | M3 | T-AGENT-10 | #49 |
| ⬜ | T-VOICE-01 | Native model servers: setup scripts and health check | M2 | T-PLAT-06 | #41 |
| ⬜ | T-VOICE-02 | Provider factory with local default and hosted switch | M2 | T-PLAT-02 | #42 |
| ⬜ | T-VOICE-03 | Pipeline tuning: VAD, turn detection, barge-in, streaming TTS, silence policy | M2 | T-VOICE-02 | #43 |
| ⬜ | T-VOICE-04 | Per-stage latency metrics | M2 | T-VOICE-03 | #44 |
| ⬜ | T-VOICE-05 | Browser playground dev mode | M2 | T-VOICE-03, T-AGENT-01 | #45 |
| ⬜ | T-VOICE-06 | Latency benchmark: local vs hosted | M4 | T-VOICE-04, T-EVAL-01 | #79 |
| ⬜ | T-TEL-01 | LiveKit server and LiveKit SIP with Redis | M3 | T-VOICE-01 | #60 |
| ⬜ | T-TEL-02 | Asterisk PBX container with extensions and trunk | M3 | T-TEL-01 | #61 |
| ⬜ | T-TEL-03 | Inbound trunk and dispatch rule for extension 100 | M3 | T-TEL-02, T-AGENT-05 | #62 |
| ⬜ | T-TEL-04 | Outbound dialling, call-result detection and SIP transfer helpers | M3 | T-TEL-02 | #63 |
| ⬜ | T-TEL-05 | Softphone provisioning and live-call checklist | M3 | T-TEL-03, T-TEL-04, T-BANK-03 | #64 |
| ⬜ | T-TEL-06 | Call recording with call_id mapping | M3 | T-TEL-02 | #65 |
| ⬜ | T-SCHED-01 | Campaign definitions and candidate queue | M3 | T-COMP-04, T-GW-10 | #54 |
| ⬜ | T-SCHED-02 | Dialer: rooms, SIP participant, pacing | M3 | T-SCHED-01, T-TEL-04 | #55 |
| ⬜ | T-SCHED-03 | Retry rules and attempt logging | M3 | T-SCHED-02 | #56 |
| ⬜ | T-SCHED-04 | Dial-time re-check of contact flags | M3 | T-SCHED-02 | #57 |
| ⬜ | T-SCHED-05 | Callbacks and missed-promise follow-ups | M3 | T-SCHED-02 | #58 |
| ⬜ | T-SCHED-06 | Scheduler service: jobs, trigger API, compose service | M3 | T-SCHED-03, T-SCHED-04, T-SCHED-05 | #59 |
| ⬜ | T-OBS-01 | PII redaction with Presidio | M2 | T-PLAT-02 | #38 |
| ⬜ | T-OBS-02 | Version stamping for prompts, policies and models | M2 | T-PLAT-04 | #39 |
| ⬜ | T-OBS-03 | Redacted transcript store | M2 | T-OBS-01 | #40 |
| ⬜ | T-OBS-04 | Langfuse stack and agent tracing | M4 | T-OBS-01, T-AGENT-08 | #77 |
| ⬜ | T-OBS-05 | Call reconstruction report | M4 | T-OBS-04, T-BANK-07, T-TEL-06 | #78 |
| ⬜ | T-CON-01 | Console skeleton and bank API client | M4 | T-BANK-07 | #67 |
| ⬜ | T-CON-02 | Campaign and single-call triggers | M4 | T-CON-01, T-SCHED-06 | #68 |
| ⬜ | T-CON-03 | Calls list and call detail | M4 | T-CON-01, T-OBS-03 | #69 |
| ⬜ | T-CON-04 | Follow-up queue | M4 | T-CON-01 | #70 |
| ⬜ | T-CON-05 | Compliance overview | M4 | T-CON-03 | #71 |
| ⬜ | T-EVAL-01 | Text-mode scenario harness with fake gateway | M2 | T-AGENT-02 | #37 |
| ⬜ | T-EVAL-02 | Persona scenario library | M4 | T-EVAL-01, T-BANK-03 | #72 |
| ⬜ | T-EVAL-03 | Deterministic guardrail checks | M4 | T-EVAL-01, T-COMP-05 | #73 |
| ⬜ | T-EVAL-04 | Simulated caller and LLM judge | M4 | T-EVAL-02 | #74 |
| ⬜ | T-EVAL-05 | Metrics report and nightly eval workflow | M4 | T-EVAL-03, T-EVAL-04 | #75 |
| ⬜ | T-EVAL-06 | Demo runbook and human review | M4 | T-TEL-05, T-EVAL-05, T-CON-03 | #76 |

## Traceability (PRD → requirements → tasks)

| PRD | Requirements | Tasks | Done |
|---|---|---|---|
| U1 | AGENT-R7, SCHED-R1 | T-AGENT-09, T-SCHED-01 | ░░░░░ 0/2 |
| U2 | COMP-R5, AGENT-R8, SCHED-R1 | T-AGENT-10, T-COMP-05, T-SCHED-01 | ░░░░░ 0/3 |
| U3 | AGENT-R1, AGENT-R5 | T-AGENT-01, T-AGENT-05, T-AGENT-06 | ░░░░░ 0/3 |
| U4 | KB-R1, KB-R6, KB-R8, AGENT-R1, AGENT-R6 | T-AGENT-01, T-AGENT-05, T-AGENT-07, T-KB-01, T-KB-06 | ░░░░░ 0/5 |
| U5 | AGENT-R10, SCHED-R1, SCHED-R6 | T-AGENT-12, T-SCHED-01, T-SCHED-05 | ░░░░░ 0/3 |
| U6 | AGENT-R3, AGENT-R9, TEL-R6 | T-AGENT-03, T-AGENT-11, T-TEL-04 | ░░░░░ 0/3 |
| F1 | VOICE-R1, VOICE-R2, VOICE-R4, VOICE-R5, VOICE-R7 | T-VOICE-01, T-VOICE-02, T-VOICE-03, T-VOICE-05 | ░░░░░ 0/4 |
| F2 | AGENT-R1, TEL-R1, TEL-R2, TEL-R3, TEL-R7 | T-AGENT-01, T-AGENT-05, T-TEL-01, T-TEL-02, T-TEL-03, T-TEL-05 | ░░░░░ 0/6 |
| F3 | BANK-R6, TEL-R1, TEL-R2, TEL-R4, TEL-R5, TEL-R7, SCHED-R1, SCHED-R3, SCHED-R4, SCHED-R7 | T-BANK-05, T-SCHED-01, T-SCHED-02, T-SCHED-03, T-SCHED-06, T-TEL-01, T-TEL-02, T-TEL-04, T-TEL-05 | ░░░░░ 0/9 |
| F4 | GW-R2, GW-R3, AGENT-R2 | T-AGENT-02, T-AGENT-04, T-GW-02, T-GW-05 | ░░░░░ 0/4 |
| F5 | BANK-R1, BANK-R3, BANK-R4, BANK-R5, GW-R1, GW-R4, GW-R11 | T-BANK-01, T-BANK-02, T-BANK-03, T-BANK-04, T-GW-01, T-GW-06, T-GW-09, T-GW-10 | ░░░░░ 0/8 |
| F6 | KB-R1, KB-R2, KB-R3, KB-R4, KB-R5, KB-R6, KB-R7, GW-R7, AGENT-R5, AGENT-R6 | T-AGENT-06, T-AGENT-07, T-GW-08, T-KB-01, T-KB-02, T-KB-03, T-KB-04, T-KB-05, T-KB-06 | ░░░░░ 0/9 |
| F7 | PLAT-R4, BANK-R7, GW-R6, AGENT-R11 | T-AGENT-08, T-BANK-06, T-GW-07, T-PLAT-04 | █░░░░ 1/4 |
| F8 | BANK-R6, COMP-R1, COMP-R2, COMP-R3, COMP-R4, COMP-R5, COMP-R6, COMP-R8, AGENT-R3, AGENT-R4, AGENT-R13, SCHED-R2, SCHED-R4, SCHED-R5 | T-AGENT-01, T-AGENT-02, T-AGENT-03, T-AGENT-04, T-AGENT-10, T-BANK-05, T-COMP-01, T-COMP-02, T-COMP-03, T-COMP-04, T-COMP-05, T-SCHED-01, T-SCHED-03, T-SCHED-04 | ░░░░░ 0/14 |
| F9 | BANK-R7, AGENT-R9, TEL-R6 | T-AGENT-11, T-BANK-06, T-TEL-04 | ░░░░░ 0/3 |
| F10 | BANK-R9, SCHED-R7, CON-R1, CON-R2, CON-R3, CON-R4, CON-R5, CON-R6 | T-BANK-07, T-CON-01, T-CON-02, T-CON-03, T-CON-04, T-CON-05, T-SCHED-06 | ░░░░░ 0/7 |
| F11 | GW-R8, AGENT-R13, OBS-R1, OBS-R3, OBS-R4, OBS-R5, OBS-R6 | T-AGENT-01, T-AGENT-02, T-GW-04, T-GW-09, T-GW-10, T-OBS-01, T-OBS-02, T-OBS-03, T-OBS-04 | ░░░░░ 0/9 |
| F12 | EVAL-R1, EVAL-R2, EVAL-R4 | T-EVAL-01, T-EVAL-02, T-EVAL-04 | ░░░░░ 0/3 |
| F13 | BANK-R8, AGENT-R12 | T-AGENT-08, T-BANK-06 | ░░░░░ 0/2 |
| F14 | VOICE-R3 | T-VOICE-02 | ░░░░░ 0/1 |
| D-OUTCOME | PLAT-R4, BANK-R1, BANK-R7, GW-R6, AGENT-R2, AGENT-R11 | T-AGENT-02, T-AGENT-04, T-AGENT-08, T-BANK-01, T-BANK-06, T-GW-07, T-PLAT-04 | █░░░░ 1/7 |
| NFR-LAT | VOICE-R4, VOICE-R6, VOICE-R8 | T-VOICE-03, T-VOICE-04, T-VOICE-06 | ░░░░░ 0/3 |
| NFR-ACC | PLAT-R6, BANK-R2, BANK-R3, BANK-R5, KB-R6, GW-R4, EVAL-R1, EVAL-R3 | T-BANK-02, T-BANK-03, T-BANK-04, T-EVAL-01, T-EVAL-03, T-GW-06, T-KB-06, T-PLAT-05 | ░░░░░ 0/8 |
| NFR-SEC | PLAT-R1, PLAT-R2, BANK-R2, GW-R1, GW-R2, GW-R5, GW-R9, GW-R10, GW-R11, OBS-R1, OBS-R2, CON-R1, CON-R7 | T-BANK-02, T-CON-01, T-GW-01, T-GW-02, T-GW-03, T-GW-04, T-GW-05, T-GW-06, T-GW-07, T-GW-08, T-GW-09, T-GW-10, T-OBS-01, T-PLAT-02 | ░░░░░ 1/14 |
| NFR-AUDIT | KB-R2, KB-R5, GW-R8, COMP-R7, TEL-R8, OBS-R3, OBS-R4, OBS-R7, CON-R4 | T-COMP-01, T-COMP-04, T-COMP-05, T-CON-03, T-GW-04, T-GW-09, T-GW-10, T-KB-02, T-KB-05, T-OBS-02, T-OBS-03, T-OBS-05, T-TEL-06 | ░░░░░ 0/13 |
| NFR-CAP | VOICE-R1, VOICE-R8, SCHED-R3 | T-SCHED-02, T-VOICE-01, T-VOICE-06 | ░░░░░ 0/3 |
| NFR-PORT | PLAT-R1, PLAT-R3, PLAT-R5, PLAT-R7, KB-R4, TEL-R1, TEL-R2 | T-KB-04, T-PLAT-01, T-PLAT-02, T-PLAT-03, T-PLAT-06, T-TEL-01, T-TEL-02 | █░░░░ 2/7 |
| AC-TASK | EVAL-R2, EVAL-R5 | T-EVAL-02, T-EVAL-05 | ░░░░░ 0/2 |
| AC-GUARD | COMP-R2, COMP-R3, COMP-R4, COMP-R6, COMP-R8, CON-R6, EVAL-R3, EVAL-R5 | T-COMP-01, T-COMP-02, T-COMP-03, T-COMP-04, T-COMP-05, T-CON-05, T-EVAL-03, T-EVAL-05 | ░░░░░ 0/8 |
| AC-GROUND | KB-R7, KB-R8, EVAL-R4, EVAL-R5 | T-EVAL-04, T-EVAL-05, T-KB-06 | ░░░░░ 0/3 |
| AC-EXP | VOICE-R6, VOICE-R8, EVAL-R4, EVAL-R6 | T-EVAL-04, T-EVAL-06, T-VOICE-04, T-VOICE-06 | ░░░░░ 0/4 |
| AC-DEMO | TEL-R7, EVAL-R6 | T-EVAL-06, T-TEL-05 | ░░░░░ 0/2 |
