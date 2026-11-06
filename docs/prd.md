# PRD — AI voice agent for loan servicing (POC)

> Agent-readable copy of [design/02-prd.html](design/02-prd.html) (v0.1, 5 Nov 2026).
> The HTML/PDF is the signed-off source; if the two disagree, the HTML wins and this file must be fixed.
> IDs in **bold** (U*, F*, NFR-*, AC-*) are stable and are referenced from `specs/**`.

## 1. Summary

A voice AI agent for a bank that calls customers about upcoming and missed loan payments, and answers
customers who call in with first-level support. It verifies who it is speaking to, looks up the
customer's loans through a secured gateway, explains the bank's policies from a knowledge base,
collects structured information (reason for non-payment, promise-to-pay date, callback preference),
and hands the call to a human whenever the situation needs one.

The POC runs entirely on one Mac against a **mock bank**: synthetic customers, loans, transactions and
policy documents. It proves the conversation design, the tool and data boundary, and the guardrails —
not scale or carrier integration.

## 2. Problem and opportunity

- Early-stage delinquency (1–30 DPD) is mostly forgetfulness, timing or temporary hardship. A timely,
  polite call resolves most of these, but human call capacity is limited.
- Inbound lines are dominated by repetitive questions ("what do I owe?", "when is it due?", "what
  happens if I'm late?", "how do I pay?").
- Every call should leave structured data behind (reason codes, promises, follow-ups).

## 3. Goals and non-goals

Goals (POC): natural, interruptible English voice over SIP; two outbound campaigns (pre-due, missed
payment) plus inbound support; answer only from bank data and policy documents, never model memory;
capture structured outcomes into the mock bank; enforce verification, disclosures and contact rules
**in code**; run fully locally with open-source components.

Non-goals (POC): taking payments or changing balances/terms/contact details; credit decisions,
restructuring or fee waivers (refer only); real carrier numbers, high concurrency, multi-language;
real core banking/CRM integration; formal regulatory sign-off.

## 4. Users

| User | Needs |
|---|---|
| Borrower (customer) | Clear info on what is owed and when, consequences of paying late, options, ability to reach a human. Treated respectfully, not called excessively. |
| Collections / support officer | Escalated calls arrive with context captured; sees promises and callbacks needing follow-up. |
| Operations / compliance lead | Configures campaigns and contact rules; reviews transcripts and outcomes; confirms disclosures and verification happened on every call. |

## 5. Core use cases

| ID | Use case | Behaviour |
|---|---|---|
| **U1** | Outbound: pre-due reminder | Call customers with an instalment due within 3 days. Verify, state amount and due date, offer payment channels, confirm intent to pay, record outcome. |
| **U2** | Outbound: missed payment (1–30 DPD) | Verify, give the collections disclosure, state overdue amount, ask why (record reason code), explain consequences from policy (late fee, bureau reporting, next stages), capture promise-to-pay date and amount. Refer hardship to a human. |
| **U3** | Inbound: account questions | Balance, next due date, last payment, payoff estimate, loan status. Verify, then answer from the gateway. |
| **U4** | Inbound: policy/product questions | "What happens if I pay late?", "Can I pay early?", "How does restructuring work?" — answered from the KB, citing the policy, **no verification required** for general questions. |
| **U5** | Follow-up and callback | Schedule a callback at the customer's preferred time; call back with context (e.g. a missed promise-to-pay). |
| **U6** | Escalation to a human | Triggered by dispute, hardship, complaint, lawyer/bankruptcy mention, request for a human, 2 failed verifications, or repeated misunderstanding. Warm transfer with spoken + written summary, or a callback ticket if no human is available. |

## 6. Functional requirements

P0 = needed for the POC demo; P1 = if time allows.

| ID | Requirement | Pri |
|---|---|---|
| **F1** | Voice conversation. Streaming STT, LLM, TTS with barge-in, end-of-turn detection, graceful silence handling (re-prompt, then politely end). | P0 |
| **F2** | Inbound calls. Answer the support extension, greet, identify intent, route to the right flow. | P0 |
| **F3** | Outbound calls. From a campaign queue or on demand from the console. Detect no-answer, busy, voicemail; retry per rules. | P0 |
| **F4** | Identity verification. Right party (full name, then DOB + last 4 of loan number) before any account data. Lock after 2 failures. Third parties get only a neutral callback message. | P0 |
| **F5** | Customer data via gateway. Read-only tools for profile, loans, schedule, recent transactions, past interactions — scoped to the verified customer only. | P0 |
| **F6** | Policy answers (RAG). Retrieve from KB, answer with source doc cited in the log. Low confidence → say so and offer follow-up instead of guessing. | P0 |
| **F7** | Information capture. Structured outcome for every call (see §8). | P0 |
| **F8** | Compliance guardrails. AI + recording disclosure at call start; collections disclosure on U2; calling-window, frequency-cap, DNC and consent checks before dialling; oral dispute or "stop calling me" honoured immediately. | P0 |
| **F9** | Human hand-off. Warm transfer to a human extension with a summary, or a callback ticket. | P0 |
| **F10** | Operations console. Trigger campaigns and single calls; view live and past calls, transcripts, tool calls, outcomes, follow-up queue. | P0 |
| **F11** | Audit and tracing. Every turn, tool call, retrieved doc and latency recorded. PII redacted in traces. Each call records prompt, policy and model versions. | P0 |
| **F12** | Simulated-caller test suite. Text-mode scenarios (cooperative, hardship, dispute, wrong person, angry, off-topic, prompt-injection) scored automatically. | P1 |
| **F13** | Post-call summary by SMS/email (mocked as an outbox table). | P1 |
| **F14** | Swappable model providers: local LLM by default, hosted LLM by configuration. | P1 |

## 7. Conversation design — missed-payment call (U2)

1. **Open:** "Hello, this is the virtual assistant from [Bank]. This call is recorded. May I speak with [first name]?"
2. **Right-party check:** third party → ask for a good time to call back, end with no details. Confirmed → verify DOB + last 4 of loan number.
3. **Disclosure:** the configured, versioned collections disclosure text.
4. **Purpose:** "Your payment of [amount] due on [date] hasn't been received yet." "Already paid" → check recent transactions, record payment-in-transit.
5. **Discover:** open-ended reason, mapped to a reason code: `forgot | insufficient_funds | income_disruption | dispute | other`.
6. **Inform:** consequences and timeline from the KB: late fee, reporting at 30 DPD, next stages.
7. **Resolve:** promise-to-pay (date within policy window, amount), or refer hardship/dispute to a human.
8. **Confirm and close:** read back the commitment, list payment channels, offer callback or human contact.

## 8. Data captured per call (**D-OUTCOME**)

`call_id`, `direction`, `campaign`, `customer_id`, `loan_id`,
`contact_result` (`right_party | third_party | no_answer | voicemail | wrong_number`), `verified`,
`disclosures_given[]`, `intent[]`, `reason_code`, `promise_to_pay{date, amount}`, `callback{datetime}`,
`escalated{reason, target}`, `dispute_flag`, `cease_contact_flag`, `sentiment`, `summary`,
`kb_sources[]`, `versions{prompt, policy, model}`, `latency_p50/p90`, `recording_uri`.

## 9. Non-functional requirements

| ID | Requirement |
|---|---|
| **NFR-LAT** | Median end-of-speech → first agent audio ≤ 2.0 s with local models, ≤ 1.2 s with a hosted LLM. Barge-in stops TTS in < 300 ms. |
| **NFR-ACC** | Zero amounts or dates that disagree with bank data (checked in tests). Policy answers come from retrieved text only. |
| **NFR-SEC** | Agent has no direct DB access; gateway is least-privilege and scoped per call. PII masked in logs. All data synthetic. Secrets in `.env`, not code. |
| **NFR-AUDIT** | Every call fully reconstructable: audio, transcript, tool calls, sources, versions. |
| **NFR-CAP** | 1–2 concurrent calls on a 16 GB M4 Mac. |
| **NFR-PORT** | Components containerised or scriptable; moving to server/cloud and real SIP trunks is configuration only. |

## 10. Success metrics and acceptance criteria

| ID | Criterion |
|---|---|
| **AC-TASK** | ≥ 80% of scripted scenarios reach the correct outcome (reason code, promise captured, or correct escalation). |
| **AC-GUARD** | 100% of calls with account data had verification first; 100% have required disclosures; zero calls outside the window or over the cap in tests; 100% of disputes and cease-contact requests honoured. |
| **AC-GROUND** | ≥ 95% of policy answers judged faithful to the cited document; zero fabricated figures. |
| **AC-EXP** | Latency targets met at P50; a human reviewer rates ≥ 4 of 5 calls "natural and respectful". |
| **AC-DEMO** | Live end-to-end inbound call, outbound missed-payment call, and warm transfer to a human softphone. |

## 11. Milestones (indicative) — see [`specs/milestones.md`](../specs/milestones.md)

| Week | Deliverable |
|---|---|
| 1 | Mock bank (schema, seed, API), policy KB ingested, data gateway with tools and audit. |
| 2 | Local voice pipeline in browser test mode; inbound U3 and U4 against gateway and KB. |
| 3 | SIP telephony (Asterisk, softphones); outbound U1 and U2 with the compliance gate; hand-off U6. |
| 4 | Console, tracing, simulated-caller suite, tuning against metrics, demo. |

## 12. Risks and open questions

- **Local LLM quality** (8B may be weak at tool calling) → strict state machine, few tools per state, hosted LLM switch.
- **Latency on a laptop** (Whisper is not truly streaming) → VAD-segmented transcription, smaller models, sentence-level TTS streaming.
- **Jurisdiction** — US rules (FDCPA / Reg F / TCPA) are the placeholder; all rules are configuration.
- **Voice and persona** — bank name, agent name, tone, whether the agent may negotiate amounts or only record them (POC: record only).
