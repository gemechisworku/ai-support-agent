---
description: Pick the next ready spec task and implement it on its milestone branch
argument-hint: "[M1]  (optional milestone filter)"
---
Run `make tasks-next ARGS="--milestone $ARGUMENTS"` (or `make tasks-next` if no argument was given).
Prefer the lowest milestone that still has ready tasks, so milestone PRs finish in order. Pick the
first listed task, unless a task in the same module that you just finished unblocks a better one.
Then follow `.claude/commands/implement-task.md` for that task id.
