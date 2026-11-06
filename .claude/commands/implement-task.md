---
description: Implement one spec task end to end (branch → tests → code → status → PR)
argument-hint: T-XXX-NN
---
Implement task **$ARGUMENTS** following the loop and Definition of Done in AGENTS.md.

1. Run `python3 scripts/tasks.py show $ARGUMENTS`. If any dependency is not `done`, stop and say which.
2. Read the module's `spec.md` and `plan.md`, the constitution, and the **Interfaces** section of any
   upstream module the task consumes.
3. Run `git switch -c task/$ARGUMENTS-<short-slug>` from an up-to-date `main`.
4. Write the tests listed under **Tests** first and confirm they fail. Then implement until they pass.
   Stay strictly within the task's scope.
5. Run `make check` and fix everything until it is green.
6. Update `tasks.md`: set `Status: done` and tick the acceptance criteria. Update `spec.md`/`plan.md`
   if reality differs. Do not edit `specs/STATUS.md` (CI regenerates it on main).
7. Commit as `feat(<module>): <summary> ($ARGUMENTS)`, push, and open a PR with `gh pr create`. Use
   the title `[$ARGUMENTS] <title>` and fill in the PR template, including `Closes #<issue>` (the
   issue number is in the task's Issue field).
8. Report what was built, which tests prove it, and the PR URL.
