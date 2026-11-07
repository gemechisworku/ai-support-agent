# 05 · Compliance rules engine — Tasks

> Each task is one GitHub issue and one PR. The field bullets are parsed by `scripts/tasks.py`, so keep
> their exact format. Status is `todo` or `done`. Issue is `—` until `make issues-sync` fills it in.
> Ids are never reused. Order tasks by dependency.

## Tasks

### T-COMP-01 · Versioned contact-rules config and loader
- **Status:** done
- **Milestone:** M2
- **Issue:** #35
- **Depends on:** T-PLAT-04
- **Requirements:** COMP-R1, COMP-R7, COMP-R8

Create `data/config/contact_rules.yaml` (values from [plan.md](plan.md#config-files)), the contracts in
`supportagent/contracts/compliance.py` (`ContactRules`, `CallingWindow`, `FrequencyCap`, `RetryRules`,
`ContactFacts`, `DenyReason`, `GateDecision`), and `config.load_rules()`. Add a consistency test that the
YAML values match the KB doc `contact-calling-rules`.

**Acceptance criteria**
- [x] `load_rules()` returns a validated model; `rules.version_tag == "contact_rules@1.0"`
- [x] Invalid files (end ≤ start, negative counts, unknown keys) raise `ValueError` naming the field
- [x] Path overridable via settings `CONTACT_RULES_PATH`
- [x] KB consistency test passes (skipped with a reason if `data/policies/contact-calling-rules.md` is absent)

**Tests**
- `tests/compliance/test_config.py` (unit): load ok; each invalid case; version tag
- `tests/compliance/test_kb_consistency.py::test_contact_rules_match_kb` (unit)

**Files:** `data/config/contact_rules.yaml`, `src/supportagent/compliance/config.py`, `src/supportagent/contracts/compliance.py`, tests above

### T-COMP-02 · Calling-window check in customer local time
- **Status:** todo
- **Milestone:** M3
- **Issue:** #50
- **Depends on:** T-COMP-01
- **Requirements:** COMP-R2

`check_calling_window(facts, now, rules) -> list[DenyReason]`. It converts `now` to `facts.timezone` with
`zoneinfo`, applies `[start, end)`, then the optional `preferred_window` (`"HH:MM-HH:MM"`, same day). It
returns `unknown_timezone` for a missing or invalid zone, and rejects naive datetimes.

**Acceptance criteria**
- [ ] Spec acceptance criteria for COMP-R2 hold, including both DST transitions in 2026
- [ ] Malformed `preferred_window` → `ValueError` at fact construction, not silent allow

**Tests**
- `tests/compliance/test_window.py` (unit): boundaries 07:59/08:00/20:59/21:00; LA/NY/UTC; DST days; preferred window; hypothesis property "allowed ⇒ local hour in [8,21)"

**Files:** `src/supportagent/compliance/rules.py`, `tests/compliance/test_window.py`

### T-COMP-03 · Frequency cap (7-in-7 and post-conversation cool-off)
- **Status:** todo
- **Milestone:** M3
- **Issue:** #51
- **Depends on:** T-COMP-01
- **Requirements:** COMP-R3

`check_frequency(facts, now, rules) -> list[DenyReason]`. It returns `frequency_cap` when
`attempts_last_7d ≥ max_attempts`, and `cool_off` when `now − last_conversation_at <
cool_off_days_after_conversation`. Both limits come from config.

**Acceptance criteria**
- [ ] Spec acceptance criteria for COMP-R3 hold (6 vs 7 attempts; 6d23h vs 7d1h)
- [ ] Changing YAML limits changes behaviour without code changes (test with a custom `ContactRules`)

**Tests**
- `tests/compliance/test_frequency.py` (unit): boundaries, custom limits, `last_conversation_at=None`

**Files:** `src/supportagent/compliance/rules.py`, `tests/compliance/test_frequency.py`

### T-COMP-04 · Eligibility gate: DNC, consent, dispute, cease-contact
- **Status:** todo
- **Milestone:** M3
- **Issue:** #52
- **Depends on:** T-COMP-02, T-COMP-03
- **Requirements:** COMP-R4, COMP-R7

`check_flags` plus `evaluate(facts, now, rules) -> GateDecision`, which combines all checks, collects every
reason, fails closed and stamps `rules_version`. Also add
`facts_from_eligible(EligibleLoan, planned_at) -> ContactFacts`. This is the single entry point the
scheduler uses (T-SCHED-01, T-SCHED-04).

**Acceptance criteria**
- [ ] Spec acceptance criteria for COMP-R4 and COMP-R7 hold
- [ ] `allowed` is true iff `reasons == []`
- [ ] Persona facts: Stop-calling Sam (cease) and any `dispute_open` loan are always denied; Frank at 10:00 local with 0 attempts is allowed
- [ ] Branch coverage of `supportagent.compliance.rules` is 100%

**Tests**
- `tests/compliance/test_gate.py` (unit): each flag; combined reasons; fail-closed on missing facts; persona-shaped facts; `facts_from_eligible` mapping

**Files:** `src/supportagent/compliance/rules.py`, `tests/compliance/test_gate.py`

### T-COMP-05 · Disclosure registry and required-disclosure checker
- **Status:** done
- **Milestone:** M2
- **Issue:** #36
- **Depends on:** T-COMP-01
- **Requirements:** COMP-R5, COMP-R6, COMP-R7, COMP-R8

Create `data/config/disclosures.yaml`, a `DisclosureRegistry` (`render`, `required_for`) and
`check_disclosures(outcome, events) -> list[Violation]`. Violation codes: `missing_disclosure:<key>`,
`data_before_verification`, `data_before_collections_disclosure`. The agent (T-AGENT-04) speaks the
rendered texts and records `disclosures_given` as `key@version`. Evals (T-EVAL-03) use the checker.

**Acceptance criteria**
- [x] Spec acceptance criteria for COMP-R5 and COMP-R6 hold
- [x] `render` fails on unknown placeholders; `RenderedDisclosure` carries `key`, `version`, `text`
- [x] Disclosure texts match the KB doc `required-disclosures` (consistency test, skipped if the doc is absent)
- [x] Branch coverage of `supportagent.compliance.disclosures` is 100%

**Tests**
- `tests/compliance/test_disclosures.py` (unit): load, render, `required_for` per direction/campaign
- `tests/compliance/test_check_disclosures.py` (unit): compliant stream; each violation code; inbound policy-only call needs no verification
- `tests/compliance/test_kb_consistency.py::test_disclosures_match_kb` (unit)

**Files:** `data/config/disclosures.yaml`, `src/supportagent/compliance/disclosures.py`, tests above
