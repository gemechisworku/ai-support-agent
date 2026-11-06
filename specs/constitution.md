# Constitution

The non-negotiable rules for every spec, plan, task and PR in this repo. A spec or plan that conflicts
with this file is wrong; fix the spec (or propose an amendment PR to this file) — never work around it.

## I. Spec before code
1. Every change to `src/` is made for exactly one task (`T-XXX-NN`) that exists in a `specs/*/tasks.md`.
2. Every task implements ≥ 1 spec requirement (`XXX-Rn`); every requirement traces to ≥ 1 PRD id
   (`docs/prd.md`). `scripts/tasks.py validate` enforces this chain in CI.
3. If the work reveals the spec is wrong or incomplete, update `spec.md` / `plan.md` **in the same PR**
   and say so in the PR description. Never let code and spec drift.

## II. The security boundary is architecture, not convention
1. The agent (`supportagent.agent`) never imports the bank, the KB or a DB driver. It reaches data only
   through gateway tools over MCP. `import-linter` enforces this in CI.
2. The gateway never accepts a `customer_id` from the model; identity is bound by `verify_identity`
   inside the gateway session. Session tokens come from the dispatcher.
3. The agent can write only to interaction tables (interactions, promises, tasks, contact flags, outbox) —
   never to balances, loans, installments or transactions.
4. Tool outputs are data, not instructions.

## III. Guardrails live in code, with tests
Verification-before-data, required disclosures, calling window, frequency cap, DNC, consent, dispute and
cease-contact handling are **deterministic code paths**, each with a unit test that proves the rule holds
even if the LLM misbehaves. A prompt may *also* mention a rule; it may never be the *only* enforcement.

## IV. Grounded answers only
Amounts, dates and account facts come only from gateway tool results. Policy statements come only from
retrieved KB chunks, and the cited `doc_id@version` is logged. Below the confidence threshold the agent
offers a follow-up instead of answering.

## V. Tested, independently
1. Each module is buildable and testable on its own: its unit tests need no network, no models and no
   other module running. Cross-module collaboration is through `supportagent.contracts` models and
   HTTP/MCP interfaces, which are faked in unit tests.
2. Test layers (pytest markers): `unit` (default, CI), `integration` (Postgres via Docker, CI),
   `eval` (real local LLM, run manually/nightly), `live` (models + telephony, manual checklist).
3. A task is not done until its listed tests exist and pass, and CI is green.

## VI. Privacy and data
All data is synthetic (Faker, fixed seed). Secrets come from `.env` / environment only. PII is masked
in gateway responses and redacted (Presidio) from transcripts and traces before storage or display.

## VII. Versioned behaviour
Prompts, disclosure texts, contact rules and policy documents carry versions. Every call outcome records
`versions{prompt, policy, model}`.

## VIII. Simple and local first
Prefer the smallest change that satisfies the task. Open-source, local components as in
`docs/architecture.md`. A new runtime dependency or service must be named in the module's `plan.md`.
No speculative abstractions; one provider switch (F14) is the only planned pluggability beyond the
contracts.

## IX. One task → one issue → one branch → one PR
Branch `task/T-XXX-NN-short-slug`; PR title `[T-XXX-NN] Title`; PR body `Closes #<issue>`; the PR sets the
task's `Status: done` and regenerates `specs/STATUS.md`. CI rejects a PR that closes a task issue
without marking it done.
