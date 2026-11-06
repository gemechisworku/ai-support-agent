# NN · Module name — Spec

> **Code:** `XXX` · **Location:** `src/supportagent/<pkg>/` · **Milestones:** M1
> **PRD:** F5, F7 · **Depends on:** PLAT (contracts) · **Status:** see [tasks.md](tasks.md)

## Purpose
Two or three sentences on what this module is responsible for, and what it is explicitly *not*
responsible for.

## Scope
**In:** …
**Out:** … (point to the module that owns it)

## Requirements
<!-- One line per requirement. The format is parsed by scripts/tasks.py — keep it exact:
     - **XXX-Rn** (P0|P1 · PRD-ID, PRD-ID) — The system shall … -->
- **XXX-R1** (P0 · F5) — The module shall …
- **XXX-R2** (P1 · F13) — …

## Acceptance criteria
Testable statements, each naming the requirement it proves. Prefer Given/When/Then for behaviour.
- **XXX-R1:** Given …, when …, then …

## Interfaces
What this module **provides** (HTTP routes, MCP tools, Python functions, CLI) and **consumes**, with
links to the `supportagent.contracts` models. Downstream modules code against this section.

## Non-functional
Latency, security, privacy, data-volume constraints that apply to this module.

## Open questions
Decisions not yet made. Resolve them before the task that depends on them starts.
