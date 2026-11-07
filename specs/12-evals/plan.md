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
- **FakeGateway:** an in-memory port of the gateway semantics, seeded from the scenario fixture:
  - scope rules: unverified tools only until bound, lock after 2 failures, typed inputs
  - masking
  - promise validation (≤ 14 days, amount ≤ due)
  - contact flags
  - an audit list equivalent to the `audit_log` rows

  A **conformance suite** (`tests/evals/test_gateway_conformance.py`) runs the same cases against
  `FakeGateway` (unit) and the real MCP gateway (integration, seeded persona DB), so the fake cannot drift.
- **ScriptedLLM:** an `livekit.agents.llm.LLM` subclass. It replays a per-scenario `agent_script` (list
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
  prompts/simulator.md  prompts/judge_faithfulness.md  prompts/judge_naturalness.md  prompts/VERSION
src/supportagent/observability/guardrails.py     CallRecord, CheckResult, check_* (owned by T-EVAL-03)
tests/scenarios/*.yaml   tests/scenarios/schema.json
tests/evals/ test_harness.py test_gateway_conformance.py test_scenarios_schema.py test_scenarios_scripted.py
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
- `scenario_id`
- `mode`
- `turns: list[TranscriptTurn]`
- `audit: list[AuditRow]`
- `outcome: CallOutcome`
- `bank_snapshot`
- `llm_model`
- `versions`

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
