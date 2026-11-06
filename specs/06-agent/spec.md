# 06 · Conversation agent — Spec

> **Code:** `AGENT` · **Location:** `src/supportagent/agent/` · **Milestones:** M2 (inbound), M3 (outbound, hand-off)
> **PRD:** U1–U6, F2, F4, F6, F7, F8, F9, F13, D-OUTCOME · **Depends on:** GW (MCP tools), VOICE (session
> factory), COMP (disclosure registry), OBS (versions, transcript store), TEL (transfer helper)
> · **Status:** see [tasks.md](tasks.md)

## Purpose
The agent worker runs one call. It joins the LiveKit room, opens a gateway session, and drives a
**deterministic state machine** of small LiveKit Agents `Agent`s. Each state has short instructions
and at most 4 domain tools. The LLM handles language inside a state. Code decides transitions,
disclosures, verification gating and the final outcome (constitution III).

It reaches data **only** through gateway MCP tools (constitution II). It does not choose whom to call
(SCHED), does not evaluate contact rules (COMP), and does not run the STT/LLM/TTS servers (VOICE).

## Scope
**In:** worker entrypoint, call context, gateway client, states and hand-offs, global interrupts,
disclosure delivery, verification flow, U1–U6 dialogue, outcome assembly, post-call summary, versioned prompts.
**Out:** the audio pipeline and silence timers (VOICE-03; the agent supplies the closing line), SIP
plumbing (TEL), campaign selection and retries (SCHED), evaluation scoring (EVAL).

## Requirements
- **AGENT-R1** (P0 · F2, U3, U4) — The worker shall start from LiveKit job dispatch, read call context from room metadata or SIP attributes, open a gateway session from code, and route inbound callers by intent to account questions, policy questions or a human.
- **AGENT-R2** (P0 · F4, D-OUTCOME) — No account-data tool shall be reachable before `verify_identity` succeeds. The state machine shall do the right-party check (outbound), then DOB + loan last 4. A third party gets only a neutral callback message, and two failed verifications escalate.
- **AGENT-R3** (P0 · U6, F8) — Global interrupt intents (human requested, dispute, stop calling, third party, lawyer/bankruptcy, complaint, hardship, repeated misunderstanding) shall be checked on every user turn by a deterministic detector before the LLM, and by a shared `report_concern` tool. A detected interrupt overrides the current state.
- **AGENT-R4** (P0 · F8) — The AI and recording disclosure shall be spoken verbatim from the versioned registry at the start of every call, and the collections disclosure before any amount on U2 calls. Each one is recorded in `disclosures_given` with its version.
- **AGENT-R5** (P0 · U3, F6) — Account answers (balance, next due date and amount, last payment, payoff estimate, status) shall come only from gateway tool results and be spoken from their `speakable` strings; the agent never computes or invents figures.
- **AGENT-R6** (P0 · U4, F6) — Policy answers shall come only from `search_policy` chunks, with the `doc_id@version` logged. When `low_confidence` is true or nothing is retrieved, the agent shall say it is not sure and offer a follow-up.
- **AGENT-R7** (P0 · U1) — The pre-due reminder flow shall verify, state the amount and due date, offer payment channels from policy, confirm intent to pay, and record the outcome.
- **AGENT-R8** (P0 · U2) — The missed-payment flow shall follow PRD §7: open → right party → verify → disclosure → purpose → already-paid check → reason code → consequences from policy → promise-to-pay (validated by the gateway) or human referral → read-back and close.
- **AGENT-R9** (P0 · U6, F9) — Escalation shall create a gateway `escalate` ticket with a written summary, speak a short summary to the caller, and warm-transfer by SIP to the human extension. If no human is available, it shall create a callback ticket and tell the caller when to expect contact.
- **AGENT-R10** (P1 · U5) — A callback call shall load the interaction history after verification and open with the context (for example a missed promise-to-pay) before continuing the normal flow.
- **AGENT-R11** (P0 · F7, D-OUTCOME) — Every call, including no-answer, third-party, locked and dropped calls, shall end with exactly one validated `CallOutcome` written via `record_call_outcome`. It carries `versions{prompt, policy, model}`, latency figures, `kb_sources[]` and the redacted transcript URI.
- **AGENT-R12** (P1 · F13) — For verified calls, the agent shall compose a short post-call summary (a deterministic template from outcome fields) that the bank places in the outbox.
- **AGENT-R13** (P0 · F11, F8) — Prompts shall be versioned files, and the bundle version is recorded on each call. Tool results shall be framed as data in prompts. The agent package shall import only `contracts`, `common`, `compliance`, `observability` and its framework libraries (import-linter).

## Acceptance criteria
- **AGENT-R2:** In text mode with the scripted LLM, when the caller asks "what's my balance?" before verifying, then no `get_loan_status` call is made and the agent enters `VerifyAgent`. For the spouse persona, the transcript contains no amount, date or product name.
- **AGENT-R3:** For each interrupt phrase list (≥ 5 phrasings per kind), `detect_interrupt` returns the right kind. In a running flow, "I want to talk to a person" during `CollectionsResolveAgent` hands off to `EscalationAgent` on that same turn.
- **AGENT-R4:** In every scripted scenario, the first agent utterance equals the registry AI and recording text, and U2 speaks the collections disclosure before the first utterance containing an amount.
- **AGENT-R5 / R8:** Frank's U2 scenario ends with `reason_code=forgot` and `promise_to_pay{date=today+3, amount=amount_due}`. Every amount in the transcript appears in a tool result.
- **AGENT-R6:** With the FakeGateway returning `low_confidence`, the reply contains no policy claim and a `request_callback` offer.
- **AGENT-R9:** Hana's scenario yields `escalated{reason=hardship, target="200"}` and one transfer call on the fake transferer. With `human_available=false`, a callback task is created instead.
- **AGENT-R11:** When the room disconnects mid-flow, then exactly one `record_call_outcome` call is made with the fields gathered so far.

## Interfaces
**Provides:** the worker entrypoint `python -m supportagent.agent.worker dev|start` (LiveKit Agents CLI,
agent name `support-agent`); `supportagent.agent.testing` with `FakeGateway`, `ScriptedLLM` and
`run_text_scenario()` for EVAL-01.

**Consumes:**
- Gateway MCP tools and the `/sessions` API ([04-gateway spec](../04-gateway/spec.md#interfaces)); models from `contracts.gateway` and `contracts.outcome`.
- Room metadata `CallMetadata{call_id, direction, campaign, loan_id?}`, set by SCHED for outbound calls. For inbound calls, the SIP participant attributes (`sip.phoneNumber`, `sip.trunkPhoneNumber`) and a generated `call_id`.
- `voice.build_session(settings)` (VOICE-02/03), returning a configured `AgentSession` and latency metrics (VOICE-04).
- `compliance.disclosures.get(kind) -> Disclosure{id, version, text}` (COMP-05).
- `observability.versions.stamp()` (OBS-02); `observability.transcripts.TranscriptStore` (OBS-03).
- A `Transferer` protocol, `transfer(room, participant_identity, to_extension) -> TransferResult`, implemented by TEL-04 and injected.

## Non-functional
- Agent-side processing per turn (interrupt detection, state logic) stays under 20 ms. The latency budget belongs to VOICE.
- Each state prompt is ≤ 250 words, so it fits small local models (risk: Qwen3 8B tool calling).
- No PII appears in logs. Transcripts go through OBS redaction before storage.

## Open questions
- Warm transfer briefing: the POC speaks the summary to the caller and writes it to the ticket. Whether the human must hear a spoken briefing before connecting (a LiveKit consult room) is P1 and decided at TEL-04.
- Whether the agent may negotiate amounts or only record them. POC: record only (PRD §12).
