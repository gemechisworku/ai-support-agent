@AGENTS.md

## Claude Code specifics
- `/next-task` picks the next ready task and runs the full loop in AGENTS.md. `/implement-task T-XXX-NN`
  does the same for a named task.
- Use plan mode for any task that touches more than one module or a `contracts` file.
- Track the task's acceptance criteria as your todo list. Each one maps to a checkbox in `tasks.md`.
- Before finishing, run `make check` and read the output. Don't claim a check passed without running it.
