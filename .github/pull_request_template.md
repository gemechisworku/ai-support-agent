<!--
Task work does NOT use this template: it goes onto the milestone branch `milestone/Mn`, and
`make milestone-pr M=Mn` creates/updates that PR with a generated body (see AGENTS.md).
This template is for `chore/*` PRs (workflow, tooling, docs). They may not close task issues.
-->
## What and why

### How it was tested

### Checklist
- [ ] `make check` is green
- [ ] No task issue closed here (task work belongs to its milestone PR)
- [ ] `specs/STATUS.md` not edited (regenerated on main after merge)
- [ ] No secrets, no real PII; new config documented in `.env.example`
