# 12 · Simulated-caller evals — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md).

## Approach
- **Harness (text mode):**
  - Build an `AgentSession` with no STT/TTS/VAD and an `llm` that is either `ScriptedLLM` (CI) or a real
    provider (eval), then start the agent's entry state for the scenario's direction and campaign.
  - The gateway client is injected: `FakeGateway` by default, or the real MCP client for integration.
  - Drive turns with `await session.run(user_input=...)` (LiveKit Agents testing API). Collect the
    `RunResult` events (messages, function calls, outputs) into a `RunRecord`.
  - Caller turns come from `caller.script` (scripted) or from the `Simulator` (simulated, until the
    agent closes or `max_turns`).
- **FakeGateway:** an in-memory port of the gateway semantics, seeded from the scenario fixture
  (`FakeBankData`, built by hand or with `FakeBankData.for_persona`). It extends `agent.testing.FakeGateway`
  (T-AGENT-02), so `run_text_scenario` accepts it, and implements every `GatewayPort` method:
  - scope rules: unverified tools only until bound, lock after 2 failures, typed inputs
  - masking
  - promise validation (≤ 14 days, amount ≤ due)
  - contact flags
  - an audit list equivalent to the `audit_log` rows

  Not modelled: rate limits, session expiry, the bank's HTTP errors, the KB (a scenario gives a `policy`
  function). A **conformance suite** (`tests/evals/test_gateway_conformance.py`) runs the same cases against
  `FakeGateway` (unit), the real tool pipeline over the persona seed in `FakeBank` (unit) and the real pipeline
  over the seeded `bank_test` database (integration), so the fake cannot drift. The real targets call
  `gateway.tools.run_tool` in process with the arguments `GatewayClient` would send, so only the MCP/HTTP hop
  is skipped. The audit rows are compared including `args_hash` (same salt, same canonical JSON).
- **ScriptedLLM:** `evals.fakes.ScriptedLLM` extends `agent.testing.ScriptedLLM` (an `livekit.agents.llm.LLM`). It replays a per-scenario `agent_script` (list
  of `{say: ...}` / `{tool: name, args: {...}}` steps), or defaults to a minimal policy that follows the
  state's expected tool. It can be told to misbehave (`misbehave: call_get_loan_status_first`) to prove
  code guardrails hold regardless of the LLM.
- **Guardrails:** pure functions over `CallRecord` (`outcome`, `audit`, `turns`, `bank_snapshot`).
  - Figures check: extract amounts (`$1,234.56`, `1234.56 dollars`) and dates (ISO, "November 12",
    "Nov 12th") from agent turns, normalise them, and require each one to appear in that call's tool
    results.
  - Disclosure check: fuzzy-match (token ratio ≥ 0.9) against the COMP-05 registry text of the version
    recorded in `outcome.versions.policy`.
- **Simulator / judge:** use the VOICE-02 provider factory to get a chat LLM and prompt for strict JSON.
  - The simulator prompt holds the persona goal, style and facts it may reveal (DOB, last 4); it never
    sees bank data beyond those.
  - The judge's per-answer prompt receives the agent answer and the cited chunk texts and returns
    `{faithful: bool, unsupported_claims: [...]}`.
  - The per-call naturalness prompt receives the redacted transcript and returns `{score: 1-5, reasons}`.
  - Prompts are versioned files under `evals/prompts/`.
- **Report:** aggregate the `ScenarioResult`s:
  - `task_success` = expected outcome matched (contact_result, reason_code, promise within tolerance,
    escalation reason)
  - `guardrail_pass` = all checks pass on every run
  - `faithfulness` = faithful ÷ judged policy answers
  - `fabricated_figures` = count of figures-check failures

  Compare against thresholds and write JSON + markdown. Exit code 1 if any threshold is missed.

## File layout
```
src/supportagent/evals/
  __init__.py  harness.py  fakes.py  scenarios.py  simulator.py  judge.py  report.py  records.py
  (T-EVAL-01 builds harness, fakes and records; `Scenario` is a dataclass there, T-EVAL-02 adds the YAML loader)
  prompts/simulator.md  prompts/judge_faithfulness.md  prompts/judge_naturalness.md  prompts/VERSION
src/supportagent/observability/guardrails.py     CallRecord, CheckResult, check_* (owned by T-EVAL-03)
tests/scenarios/*.yaml   tests/scenarios/schema.json
tests/evals/ conftest.py desk.py targets.py test_harness.py test_gateway_conformance.py test_scenarios_schema.py test_scenarios_scripted.py
             test_guardrails.py test_judge.py test_report.py test_eval_llm.py (eval) test_review_sheet.py
.github/workflows/eval.yml   docs/demo.md   docs/review-sheet.md
```

## Data model / contracts
Scenario YAML:
```yaml
id: forgetful-frank-missed-payment
type: cooperative            # cooperative|hardship|dispute|wrong_person|angry|off_topic|prompt_injection
persona: 9002                # BANK persona id (fixture below must match bank seed)
direction: outbound          # outbound|inbound
campaign: missed_payment     # pre_due|missed_payment|callback|null
fixture: {first_name: Frank, dob: 1985-03-14, loan_last4: "3310", amount_due: "412.50", due_date: -5d, ...}
caller:
  goal: "You forgot to pay. You can pay the full amount in 3 days. Be friendly."
  style: friendly
  script: ["Yes, speaking.", "March 14, 1985", "3 3 1 0", "Oh, I just forgot.", "I can pay on Friday."]
agent_script: null           # optional ScriptedLLM steps; default policy otherwise
expect:
  contact_result: right_party
  verified: true
  reason_code: forgot
  promise_to_pay: {within_days: 3, amount: amount_due}
  escalated: null
  flags: {dispute: false, cease_contact: false}
  disclosures: [ai_recording, collections]
```

`RunRecord`, which equals `CallRecord` plus metadata, has these fields:
- `scenario_id`, `mode`, `call_id` (a UUID4 derived from the scenario id)
- `turns: list[TranscriptTurn]` (timestamps are the frozen clock plus the turn number in seconds)
- `tool_calls: list[ToolCallLog]`: every gateway request with `ok`, `error_code` and the audit `decision`
- `model_calls: list[ModelCall]`: what the model asked for, including calls that never reached the gateway
- `audit: list[AuditRow]`
- `outcome: CallOutcome` (the agent's own `record_call_outcome` if it wrote one, else a draft from
  `FakeGateway.draft_outcome`; T-AGENT-08's builder replaces the draft)
- `bank_snapshot` (promises, tasks, contact preferences, interactions)
- `llm_model`, `versions`, `states`

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| CI mode | Scripted LLM + FakeGateway | Deterministic; tests code guardrails, not model quality |
| Fake drift | Conformance suite against the real gateway | One suite, two targets |
| Checker location | `observability/guardrails.py` | Console and report reuse them; observability is importable by all |
| Judge | Hosted Haiku if a key is set, else local Llama 3.1 8B | Better judge quality; local fallback keeps it offline |
| Persona data | Duplicated into scenario fixtures, cross-checked by a test | `evals` must not import `bank` in src |

## Dependencies
- Runtime: `livekit-agents` (already from AGENT), `pyyaml`, `jsonschema`, `rapidfuzz` (disclosure fuzzy match).
- CI workflow: `.github/workflows/eval.yml` (`workflow_dispatch` + `schedule: "0 3 * * *"`). It uses
  the `ANTHROPIC_API_KEY` secret and skips with a notice if the key is absent.

## Test strategy
- **unit:**
  - harness determinism
  - FakeGateway conformance
  - scenario schema and coverage
  - the whole library in scripted mode (`test_scenarios_scripted.py`, parametrised by scenario)
  - guardrail checkers with crafted records
  - judge and report aggregation with a fake LLM
- **integration:** the conformance suite against the real gateway and the seeded bank DB.
- **eval:** `test_eval_llm.py` runs the simulated + judged library, and judge agreement on the labelled set.
- **live:** the demo runbook (manual).

## Risks
- LiveKit Agents testing APIs change. Wrap them in `harness.py` so only one file needs updating.
- Scripted scenarios pass while real-LLM ones fail. That is the intended gap: AC metrics come from
  `make eval`, not CI.
- Judge bias. Mitigated by the labelled agreement set and by recording the judge model and prompt version.

## Harness notes (T-EVAL-01)
- `run_scenario(scenario, *, mode="scripted", llm=None, gateway=None, agent=None)`. `agent` is a factory for the
  entry state; the default looks up the registered `greeting` state and raises `NoEntryState` until T-AGENT-04
  registers it. `mode="simulated"` raises `NotImplementedError` until T-EVAL-04.
- The harness freezes the clock at 2026-11-09T14:00Z and derives the call id from the scenario id, so a scripted
  scenario serialises to the same JSON on every run.
- `misbehave="call_get_loan_status_first"` makes the model call `get_loan_status` on its first request. When the
  state offers that tool the gateway denies it as `not_verified`; a state without the tool makes LiveKit reject
  the call (it never reaches the gateway, and `model_calls` shows it).
- Unit tests run with sockets blocked (`tests/evals/conftest.py::no_network`).
- `make eval-scripted` runs `tests/evals` without the integration, eval and live markers.
