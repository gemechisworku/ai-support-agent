# Milestones

Parsed by `scripts/tasks.py` (heading `## Mn · Title`, `**Due:**`, `**Goal:**`) and mirrored to GitHub
milestones by `make issues-sync`. Dates are indicative (PRD §11, weeks starting Mon 9 Nov 2026).
A milestone is done when all its tasks are done **and** its exit demo works.

## M0 · Foundation
**Due:** 2026-11-10
**Goal:** Shared config, database plumbing, contracts and test fixtures so every module can start in parallel; CI green on main.

Exit demo: `make up && make test-all` passes on a clean clone; CI green.

## M1 · Data and knowledge
**Due:** 2026-11-13
**Goal:** Mock bank (schema, seed, API), policy knowledge base ingested with hybrid retrieval, MCP data gateway with scoped tools and audit (PRD week 1).

Exit demo: with `docker compose up`, a script opens a gateway session, verifies Forgetful Frank,
reads his loan status, searches "late fee personal loan" and records a promise — and the audit log shows
every call.

## M2 · Conversation
**Due:** 2026-11-20
**Goal:** Local voice pipeline in browser/playground mode; agent state machine with inbound flows U3 and U4 against the gateway and KB; PII redaction (PRD week 2).

Exit demo: talk to the agent in the LiveKit playground: ask a policy question (cited), then verify and
ask for your balance. Text-mode tests cover verify, U3, U4 and global interrupts.

## M3 · Telephony and outbound
**Due:** 2026-11-27
**Goal:** Asterisk + LiveKit SIP + softphones; compliance gate and scheduler; outbound U1 and U2; callbacks U5; hand-off U6 (PRD week 3).

Exit demo: the scheduler places a missed-payment call to Forgetful Frank's softphone, captures a promise,
and a Hardship Hana call warm-transfers to the human softphone. Stop-calling Sam is never dialled.

## M4 · Operations, quality and demo
**Due:** 2026-12-04
**Goal:** Operations console, Langfuse tracing, simulated-caller eval suite, tuning against the PRD §10 metrics, and the end-to-end demo (PRD week 4).

Exit demo: AC-DEMO from the PRD; the eval report shows AC-TASK, AC-GUARD and AC-GROUND met.
