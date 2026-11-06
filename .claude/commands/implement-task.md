---
description: Implement one spec task end to end on its milestone branch (tests → code → status → commit → milestone PR)
argument-hint: T-XXX-NN
---
Implement task **$ARGUMENTS** following the loop and Definition of Done in AGENTS.md.

1. Run `python3 scripts/tasks.py show $ARGUMENTS`. If any dependency is not `done`, stop and say which.
   Note its milestone `Mn`.
2. Read the module's `spec.md` and `plan.md`, the constitution, and the **Interfaces** section of any
   upstream module the task consumes.
3. `git fetch origin`, then switch to `milestone/Mn`: `git switch milestone/Mn && git pull --ff-only`
   if it exists (locally or on origin), else `git switch -c milestone/Mn origin/main`.
4. Write the tests listed under **Tests** first and confirm they fail. Then implement until they pass.
   Stay strictly within the task's scope.
5. Run `make check` and fix everything until it is green.
6. Update `tasks.md`: set `Status: done` and tick the acceptance criteria. Update `spec.md`/`plan.md`
   if reality differs. Do not edit `specs/STATUS.md` (CI regenerates it on main).
7. Make ONE commit for the task: `feat(<module>): <summary> ($ARGUMENTS)`. Push `milestone/Mn`
   (retry on network errors), then run `make milestone-pr M=Mn`, which creates the draft PR or
   regenerates its body. Wait for CI on the PR and fix failures with `fix(<module>): … ($ARGUMENTS)`.
8. If that was the milestone's last todo task, run `gh pr ready <n>`. Never merge.
9. Report what was built, which tests prove it, the commit, and the milestone PR URL.
