# 06 · Conversation agent — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md). Design source: [architecture §2–3](../../docs/architecture.md#3-agent-design), [PRD §7](../../docs/prd.md#7-conversation-design--missed-payment-call-u2).

## Approach
**Worker.** `worker.py` defines the LiveKit Agents entrypoint. On a job, it:
1. parses `CallMetadata` (room metadata, or SIP attributes for inbound calls);
2. calls `POST /sessions` with the worker token, in code; the token is kept in `CallState` and never put in a prompt;
3. builds a `GatewayClient` (MCP `ClientSession` over streamable HTTP with the bearer header);
4. builds the `AgentSession` via `voice.build_session()`, with `userdata=CallState`;
5. starts at `GreetingAgent`;
6. registers a shutdown callback that always writes the outcome (`finalize()`).

**Tools are wrapped, not auto-exposed.** LiveKit's automatic MCP tool exposure is **not** used. Each
state declares explicit `@function_tool` wrappers that call `GatewayClient`. This keeps a per-state
allow-list (≤ 4 domain tools plus the shared `report_concern`), typed arguments, and a code-side
verification check. The check is defence in depth: the gateway enforces it too. `record_call_outcome`
is never wrapped as an LLM tool.

**Code-driven transitions.** A tool wrapper returns `(next_agent, message)` for a LiveKit hand-off. The
decision is made in Python from tool results (for example `verified=true` → the next state for this
direction and campaign), not by the LLM.

**Global interrupts.** `BaseStateAgent.on_user_turn_completed` runs `detect_interrupt(text)` (regex and
keyword lists per kind) before the LLM sees the turn. On a hit, it stops the reply and hands off to
the handler. The LLM-side catch-all is the shared `report_concern(kind)` tool, present in every
state. The handlers are:

| Interrupt | Handler |
|---|---|
| human_requested, complaint, legal (lawyer/bankruptcy), hardship | `EscalationAgent` |
| dispute | verified → `set_contact_flag(dispute)` then `EscalationAgent`; unverified → `EscalationAgent(reason=dispute)`. The agent never argues. |
| stop_calling | verified → `set_contact_flag(cease_contact)`, confirm, close; unverified → `request_callback(reason="cease request")` ticket, close |
| third_party | `ThirdPartyAgent` |
| misunderstanding | counter ≥ 2 → `EscalationAgent(reason=misunderstanding)` |

## States and tool allow-lists
| State | On enter (code) | LLM tools (≤ 4) | Exits |
|---|---|---|---|
| `GreetingAgent` | `say(AI+recording disclosure)`; outbound: `lookup_by_phone`, ask for first name | `confirm_right_party`, `third_party_answered`, `wrong_number` | Verify / ThirdParty / Closing |
| `VerifyAgent` | ask for full name, DOB, last 4 | `verify_identity` | campaign state / Router / Escalation (locked) |
| `ThirdPartyAgent` | neutral message, no details | `record_callback_time`, `end_call` | end |
| `RouterAgent` (inbound) | "How can I help?" | `route_account_question`, `route_policy_question`, `request_human` | Verify→Account / Policy / Escalation |
| `AccountAgent` (U3) | — | `get_loan_status`, `get_recent_transactions`, `get_interaction_history`, `finish` | Router / Closing |
| `PolicyAgent` (U4) | — | `search_policy`, `offer_follow_up`, `finish` | Router / Closing |
| `ReminderAgent` (U1) | `get_loan_status` → speak amount and date | `search_policy`, `record_intent_to_pay`, `finish` | Closing |
| `CollectionsPurposeAgent` (U2 §7 steps 3–5) | `say(collections disclosure)`; `get_loan_status` → purpose line | `get_recent_transactions`, `set_reason_code`, `mark_payment_in_transit` | Resolve / Closing |
| `CollectionsResolveAgent` (U2 §7 steps 6–7) | — | `search_policy`, `record_promise_to_pay`, `request_callback`, `refer_to_human` | Closing / Escalation |
| `CallbackAgent` (U5) | `get_interaction_history` → context line | `get_loan_status`, `record_promise_to_pay`, `request_callback`, `finish` | Closing |
| `EscalationAgent` (U6) | compose summary | `escalate_and_transfer`, `create_callback_ticket` | end |
| `ClosingAgent` | read back commitments; payment channels | `end_call`, `offer_callback` | end |

Reason codes (`contracts.outcome.ReasonCode`): `forgot | insufficient_funds | income_disruption | dispute | other`.
`set_reason_code` maps the caller's words through an LLM argument enum. Hardship (`income_disruption`)
in U2 leads to `refer_to_human` with reason `hardship`.

## File layout
```
src/supportagent/agent/
  worker.py           # AgentServer entrypoint `support-agent`; setup_call/teardown_call (testable seam)
  context.py          # parse_call_metadata(), CallState (userdata, token never in repr) + OutcomeBuilder (T-AGENT-08)
  gateway_client.py   # GatewayPort protocol, GatewayClient (typed async methods over MCP), open/close_session; verified-guard (T-AGENT-04)
  interrupts.py       # InterruptKind, detect_interrupt(), phrase lists
  base.py             # BaseStateAgent (interrupt hook, report_concern, prompt loading)
  states/             # greeting.py verify.py third_party.py router.py account.py policy.py
                      # reminder.py collections.py callback.py escalation.py closing.py
  prompts/            # <state>.md with front matter {version}; registry.py computes the bundle hash
  summary.py          # deterministic post-call summary template (F13)
  testing.py          # FakeGateway (persona data), ScriptedLLM, FakeTransferer, run_text_scenario()
tests/agent/          # mirrors the modules above
```

## Call setup (T-AGENT-01)
- `contracts/call.py` owns `CallMetadata{call_id, direction, campaign, customer_id?, loan_id?, callback_task_id?,
  caller_phone?}` (`caller_phone` is the inbound SIP number, passed on to `POST /sessions`).
- `parse_call_metadata(room_metadata, sip_attributes)`: room metadata JSON wins (a missing `call_id` is a generated
  UUID4); else `sip.phoneNumber` gives an inbound call (`campaign=inbound`, a non-E.164 number such as "anonymous"
  becomes no `caller_phone`); else a **playground** call (`direction=inbound`, `campaign=playground`). Bad
  metadata raises `InvalidCallMetadata` without echoing the content.
- `GatewayClient` keeps one MCP session open per call (`async with`), sending `Authorization: Bearer <session token>`.
  Every method returns `ToolResult[Output]`; `ok=false` carries the gateway's `ToolError`. A transport, protocol or
  schema failure becomes `ToolError(code="upstream_error")`, so states handle one shape; the log line has the tool
  name and the exception type only. `GatewayPort` is the protocol states depend on.
- `setup_call()` = parse → `open_session` (`POST /sessions`, worker credential) → connect `GatewayClient` →
  `CallState`; `teardown_call()` closes the MCP client and `DELETE /sessions/{call_id}` (best effort).
  The entrypoint only wires these to the job. The `AgentSession`, states and outcome writer come with
  T-VOICE-03 and T-AGENT-02..08.
- The entrypoint is `agents.AgentServer` with `@server.rtc_session(agent_name="support-agent")`
  (livekit-agents 1.8); the room metadata falls back to the job metadata.

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| Framework | LiveKit Agents multi-agent hand-offs | Architecture choice; built-in text-mode testing |
| Tool exposure | Explicit wrappers per state | Allow-list, typing and code-side gating (constitution II/III) |
| Disclosures | `session.say()` verbatim from the registry | Must be exact and versioned; never paraphrased by the LLM |
| Figures | Speak the gateway `speakable` strings | NFR-ACC: no model-computed numbers |
| Summary | Template, not LLM | Zero fabrication; F13 is P1 |
| Sentiment | One post-call LLM classification into the enum, falling back to `unknown` | Cheap; it does not affect the flow |

## Dependencies
`livekit-agents` (and the plugins VOICE selects), `mcp` (client), `httpx2` (the MCP SDK's HTTP client, declared
because the bearer header is set on it; T-AGENT-01). Settings: `GATEWAY_URL`, `GATEWAY_WORKER_TOKEN`, `BANK_NAME`, `AGENT_NAME`, `HUMAN_AGENT_EXTENSION`.

## Test strategy
- **unit:** `detect_interrupt` phrase tables; `OutcomeBuilder` validation; `GatewayClient` against an in-memory fake MCP server; state transitions by calling the tool wrappers with `FakeGateway`; full text-mode flows with `AgentSession` + `ScriptedLLM` (tool calls scripted per turn) for Frank, Hana, Priya, Olivia, the spouse and Sam; a static test that `record_call_outcome` is in no state's tool list and that each state has ≤ 4 domain tools.
- **eval:** the same scenarios with the real local LLM on llama.cpp (`-m eval`) via EVAL-01, run before merging prompt changes.
- **live:** the playground (VOICE-05) and telephony checklist (TEL-05).

## Risks
- The ScriptedLLM may not fit a given LiveKit Agents version's `llm.LLM` interface. If so, pin the version and adapt, or test the wrappers and transitions directly and leave full flows to `eval`.
- Llama 3.1 8B may skip tool calls. Mitigations: on-enter actions in code, a small tool set, and `tool_choice` set to required in states that must act.
