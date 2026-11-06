# 05 · Compliance rules engine — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md). Background: [research §2](../../docs/design/01-research-findings.html), [architecture §2](../../docs/architecture.md).

## Approach
Configuration-as-data plus pure functions. Two YAML files under `data/config/` are versioned in git and
loaded into Pydantic models. Every check is a small function `(facts, now, rules) -> list[DenyReason]`,
and `evaluate` concatenates them. It never short-circuits, so the console and audit see all the reasons.
It fails closed: missing timezone or missing preference facts → deny. The scheduler calls `evaluate`
twice, when queueing and again at dial time. The disclosure registry is shared by the agent (what to say)
and evals/tests (`check_disclosures`, what must have been said).

## Config files
`data/config/contact_rules.yaml`
```yaml
version: "1.0"
jurisdiction: US-placeholder         # Reg F §1006.6(b), §1006.14(b); TCPA consent for AI voice
calling_window: { start: "08:00", end: "21:00" }      # customer local time, end exclusive
frequency:
  max_attempts: 7                    # per loan
  window_days: 7
  cool_off_days_after_conversation: 7
retries:                             # consumed by SCHED, validated here
  no_answer_retry_minutes: 120
  busy_retry_minutes: 30
  voicemail_retry_minutes: 240
  max_attempts_per_day: 2
campaigns:
  pre_due_reminder: { due_within_days: 3 }
  missed_payment: { dpd_min: 1, dpd_max: 30 }
```
`data/config/disclosures.yaml`
```yaml
version: "1.0"
disclosures:
  ai_recording:
    text: "Hello, this is {agent_name}, the virtual assistant from {bank_name}. This call is recorded."
    required: { all: true }
  collections:
    text: "This is an attempt to collect a debt, and any information obtained will be used for that purpose."
    required: { campaigns: [missed_payment] }
  third_party_neutral:
    text: "This is {bank_name} calling for {first_name}. Please ask them to call us back at their convenience. Thank you."
    required: {}
```

## File layout
```
data/config/contact_rules.yaml, data/config/disclosures.yaml
src/supportagent/compliance/
  config.py        load_rules(), load_disclosures(); default paths from settings
  rules.py         check_calling_window, check_frequency, check_flags, evaluate, facts_from_eligible
  disclosures.py   DisclosureRegistry, required_for, render, check_disclosures
src/supportagent/contracts/compliance.py
tests/compliance/ test_config.py test_window.py test_frequency.py test_gate.py
                  test_disclosures.py test_check_disclosures.py test_kb_consistency.py
```

## Data model / contracts
- `ContactFacts{loan_id, customer_id, timezone: str|None, preferred_window: str|None, dnc, consent_voice_ai,
  cease_contact, dispute_open, attempts_last_7d: int, last_conversation_at: datetime|None, planned_at: datetime}`
- `GateDecision{allowed: bool, reasons: list[DenyReason], rules_version: str, evaluated_at: datetime}`
- `CallEvent{ts, kind, name}`: produced by the agent/gateway audit trail; `kind="tool"` with `name` such
  as `get_loan_status`; `kind="verified"`; `kind="disclosure"` with `name=key`.
- Account-data tools for the R6 check: `get_loan_status`, `get_recent_transactions`, `get_interaction_history`.

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| Window end | exclusive (21:00 not allowed) | conservative reading of "before 9 pm" |
| Attempt counting | trailing 7×24 h from `now`, supplied by the bank | Reg F 7-in-7 presumption |
| Cool-off | any `right_party` conversation on the loan within 7 days | Reg F §1006.14(b)(2)(i)(B) |
| Evaluation | collect all reasons, fail closed | audit clarity; safety |
| Clock | injected `now`, tz-aware | determinism (constitution III) |
| Inbound | gate not applied; disclosures are | customer-initiated |

## Dependencies
`pyyaml`. `zoneinfo` is stdlib (`tzdata` package only if the slim image lacks zone data). Dev:
`hypothesis` for window and DST property tests.

## Test strategy
- **unit (only):** every rule and reason branch, DST boundaries (2026-11-01 US fall back, 2026-03-08
  spring forward), preferred-window parsing, fail-closed paths, disclosure rendering and `required_for`,
  violation detection, config validation errors, YAML↔KB doc consistency (reads `data/policies/*.md`,
  skipped if absent). Coverage gate 100% branches for `supportagent.compliance`.
- No integration tests. The scheduler (T-SCHED-01/04) and evals (T-EVAL-03) cover integration use.

## Risks
- Rule ambiguity across jurisdictions: values live in YAML and the code stays generic.
- Bank-supplied attempt counts could be stale. SCHED re-checks at dial time with fresh facts.
