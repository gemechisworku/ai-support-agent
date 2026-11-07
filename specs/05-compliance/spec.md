# 05 · Compliance rules engine — Spec

> **Code:** `COMP` · **Location:** `src/supportagent/compliance/`, `data/config/` · **Milestones:** M2, M3
> **PRD:** F8, AC-GUARD, U2, NFR-AUDIT · **Depends on:** PLAT (contracts)
> **Status:** see [tasks.md](tasks.md)

## Purpose
Contact rules and disclosures are enforced **in code, before the model is involved** (constitution III).
This module is a pure, deterministic library: given versioned configuration and the facts about a loan
and customer, it decides whether a call may be placed now, and which disclosures a call requires. It also
checks afterwards that they were given. It has no I/O beyond reading its YAML config, no database and no
network, so every rule is unit-testable with an injected clock.

## Scope
**In:** versioned `data/config/contact_rules.yaml` and `data/config/disclosures.yaml`; the calling-window
check in the customer's local time (including the preferred window); the frequency cap and post-conversation
cool-off; the DNC, consent, dispute and cease-contact gate; the disclosure registry and the
required-disclosure checker.
**Out:** fetching facts (SCHED via the gateway service scope; BANK supplies them); dialling and retries
(SCHED, which reads `retries` from the same config file); speaking disclosures (AGENT); setting flags
(GW `set_contact_flag`). Jurisdiction: US Reg F / TCPA values are **configurable placeholders** (PRD §12),
not legal advice.

## Requirements
- **COMP-R1** (P0 · F8) — Contact rules shall be loaded from versioned `data/config/contact_rules.yaml` into a validated `ContactRules` model (`version`, `jurisdiction`, `calling_window`, `frequency`, `retries`). Invalid config fails at load with a precise error.
- **COMP-R2** (P0 · F8, AC-GUARD) — `check_calling_window(facts, now)` shall allow outbound calls only between 08:00 and 21:00 (configurable) in the customer's IANA timezone, DST-correct, and inside the customer's `preferred_window` when one is set.
- **COMP-R3** (P0 · F8, AC-GUARD) — `check_frequency(facts, now)` shall deny when outbound attempts for the loan in the trailing 7 days are ≥ 7, or when a right-party conversation happened within the last 7 days (both configurable).
- **COMP-R4** (P0 · F8, AC-GUARD) — `evaluate(facts, now, rules) -> GateDecision` shall deny with every applicable reason (`dnc`, `no_consent`, `cease_contact`, `dispute_open`, `outside_window`, `outside_preferred_window`, `frequency_cap`, `cool_off`, `unknown_timezone`), and shall fail closed on missing facts.
- **COMP-R5** (P0 · F8, U2) — A disclosure registry shall load versioned texts from `data/config/disclosures.yaml`: `ai_recording` (every call), `collections` (U2 missed-payment calls), `third_party_neutral` (message for non-customers). It shall render `{bank_name}` and `{agent_name}`, and `required_for(direction, campaign)` shall return the keys a call must deliver.
- **COMP-R6** (P0 · AC-GUARD, F8) — `check_disclosures(outcome, events)` shall return violations when a required disclosure is missing, or when account data was revealed before verification or before the collections disclosure on a U2 call.
- **COMP-R7** (P0 · NFR-AUDIT) — Every `GateDecision` and rendered disclosure shall carry the config version (`contact_rules@1.0`, `disclosures@1.0`) so the call outcome can record `versions.policy`.
- **COMP-R8** (P0 · AC-GUARD, F8) — The values in the YAML config shall agree with the KB documents `contact-calling-rules` and `required-disclosures` (window, cap, cool-off, disclosure texts), checked by a test.

## Acceptance criteria
- **COMP-R2:** Given a customer in `America/Los_Angeles`, when `now` is 2026-11-09T15:30Z (07:30 local),
  then the decision is denied with `outside_window`; at 16:00Z (08:00 local) it is allowed. On a DST
  transition day the local hour is still correct.
- **COMP-R2:** Given `preferred_window="18:00-20:00"` and 12:00 local, then the decision is denied with `outside_preferred_window`.
- **COMP-R3:** Given 7 attempts in the last 7×24 h, then `frequency_cap`; given 6, allowed. Given a right-party
  conversation 6 days 23 h ago, then `cool_off`; at 7 days 1 h, allowed.
- **COMP-R4:** Given `dnc=true` and `cease_contact=true`, then the decision is denied with both reasons. Given
  `timezone=None`, then the decision is denied with `unknown_timezone`. Stop-calling Sam (cease flag) is never allowed.
- **COMP-R5:** `required_for("outbound", "missed_payment") == ["ai_recording", "collections"]`;
  `required_for("inbound", None) == ["ai_recording"]`.
- **COMP-R6:** Given an event stream where `get_loan_status` ran before `verified`, then the violation is
  `data_before_verification`. Given a U2 call whose outcome lacks `collections`, then the violation is `missing_disclosure:collections`.
- **COMP-R7:** `evaluate(...).rules_version == "contact_rules@" + yaml.version`.
- **COMP-R8:** Changing the window in YAML without updating the KB doc makes the consistency test fail.

## Interfaces
**Provides** (pure Python, importable by any module):
- `supportagent.compliance.config`: `load_rules(path=None) -> ContactRules` (default path: settings
  `CONTACT_RULES_PATH`; relative paths fall back to the repo root), plus the shared `read_yaml` / `validate`
  helpers that turn any config problem into a `ValueError` naming the file and field
- `supportagent.compliance.rules`: `check_calling_window`, `check_frequency`,
  `evaluate(facts, now, rules) -> GateDecision`, `facts_from_eligible(EligibleLoan, planned_at) -> ContactFacts`
- `supportagent.compliance.disclosures`: `load_disclosures(path=None) -> DisclosureRegistry`,
  `registry.render(key, bank_name, agent_name) -> RenderedDisclosure`, `registry.required_for(direction, campaign)`,
  `check_disclosures(outcome: CallOutcome, events: list[CallEvent]) -> list[Violation]`
- Contracts in `supportagent/contracts/compliance.py`: `ContactRules`, `CallingWindow`, `FrequencyCap`,
  `RetryRules`, `ContactFacts`, `DenyReason` (StrEnum), `GateDecision`, `Disclosure`, `RenderedDisclosure`,
  `CallEvent{ts, kind: "tool"|"disclosure"|"verified"|"utterance", name}`, `Violation{code, detail}`.

**Consumes:** `supportagent.contracts.outcome.CallOutcome`, `supportagent.contracts.bank.EligibleLoan`
(for `facts_from_eligible`), `supportagent.common.settings` (config paths, `BANK_NAME`, `AGENT_NAME`).

## Non-functional
- Pure functions with an injected `now: datetime` (tz-aware; naive rejected), with no `datetime.now()` inside.
- 100% branch coverage on `rules.py` and `disclosures.py` (CI-enforced via `--cov-fail-under` for this package).
- `evaluate` < 1 ms per call, so the scheduler can re-check at dial time (T-SCHED-04).

## Open questions
- Jurisdiction remains a US placeholder (PRD §12). A real deployment swaps YAML values, not code.
- Promise-to-pay window (14 days) and partial payments: the gateway reads them from its own settings
  (`PROMISE_MAX_DAYS`, see the gateway spec's open questions). If they move here, add a `promises:` section
  to `contact_rules.yaml` (or a separate versioned file) with a KB consistency check against
  `promise-to-pay-rules`, and switch the gateway's `record_promise_to_pay` to read it.
