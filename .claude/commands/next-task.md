---
description: Pick the next ready spec task and implement it
argument-hint: "[M1]  (optional milestone filter)"
---
Run `make tasks-next ARGS="$ARGUMENTS"` (or `make tasks-next` if no argument was given). Pick the first
listed task, unless a task in the same module that you just finished unblocks a better one. Check that
no open PR already exists for it (`gh pr list --search "<task id>"`). Then follow
`.claude/commands/implement-task.md` for that task id.
