## [T-XXX-NN] Title

Closes #<!-- task issue number -->

### What
<!-- One paragraph: what changed and why. Link the task in specs/NN-module/tasks.md. -->

### How it was tested
<!-- Tests added/changed and how to run them. Paste `make check` tail if useful. -->

### Checklist (Definition of done — AGENTS.md)
- [ ] Tests listed in the task exist and pass (`make check`)
- [ ] Task set to `- **Status:** done` and its acceptance criteria ticked in `tasks.md`
- [ ] `spec.md` / `plan.md` updated if the implementation differs from them
- [ ] `make tasks-status` run (STATUS.md regenerated)
- [ ] No secrets, no real PII; new config documented in `.env.example`
- [ ] New dependency or service named in the module's `plan.md`
