# NN · Module name — Tasks

> Each task is one GitHub issue and one PR. The field bullets are parsed by `scripts/tasks.py`, so keep
> their exact format. Status is `todo` or `done`. Issue is `—` until `make issues-sync` fills it in.
> Ids are never reused. Order tasks by dependency.

## Tasks

### T-XXX-01 · Short imperative title
- **Status:** todo
- **Milestone:** M1
- **Issue:** —
- **Depends on:** T-PLAT-02
- **Requirements:** XXX-R1

What to build and why, in two to four sentences. Link the relevant section of [plan.md](plan.md).

**Acceptance criteria**
- [ ] Observable outcome 1
- [ ] Observable outcome 2

**Tests**
- `tests/<pkg>/test_x.py::test_…` (unit): what it proves
- `tests/<pkg>/test_y.py` (integration): what it proves

**Files:** `src/supportagent/<pkg>/x.py`, `tests/<pkg>/test_x.py`
