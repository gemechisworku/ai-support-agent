#!/usr/bin/env python3
"""Spec/task tooling: the single entry point that keeps PRD → spec → task → GitHub issue traceable.

Source of truth is markdown in the repo:
  docs/prd.md                 PRD ids  (**U1**, **F4**, **NFR-SEC**, **AC-GUARD**, **D-OUTCOME**)
  specs/milestones.md         milestones (## M1 · Title, **Due:** YYYY-MM-DD)
  specs/NN-module/spec.md     requirements  "- **BANK-R1** (P0 · F5, D-OUTCOME) — text"
  specs/NN-module/tasks.md    tasks         "### T-BANK-01 · Title" + field bullets

Commands (stdlib only, Python >= 3.9):
  validate            check ids, fields, deps and full traceability; exit 1 on error
  status [--write|--check]   print progress / regenerate / verify specs/STATUS.md
  next [--milestone M1]      tasks that are todo and whose dependencies are all done
  show T-ID           print one task with its spec/plan paths
  sync-issues [--dry-run] [--update]   create labels, milestones and one issue per task
  pr-body Mn                 the generated body of milestone Mn's PR (task checklist + Closes lines)
  milestone-pr Mn            create (draft) or update the PR for branch milestone/Mn
  check-pr --body-file F --branch B [--ready]   enforce one PR per milestone (see check_pr)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS = ROOT / "specs"
PRD = ROOT / "docs" / "prd.md"
STATUS_FILE = SPECS / "STATUS.md"

STATUSES = ("todo", "done")
NONE = {"—", "-", "", "none", "n/a"}

PRD_ID_RE = re.compile(r"\*\*((?:U|F)\d+|NFR-[A-Z]+|AC-[A-Z]+|D-[A-Z]+)\*\*")
MILESTONE_RE = re.compile(r"^## (M\d+) · (.+)$")
REQ_RE = re.compile(r"^- \*\*([A-Z]+-R\d+)\*\* \((P[01]) · ([^)]+)\) — (.+)$")
TASK_RE = re.compile(r"^### (T-([A-Z]+)-\d{2}) · (.+)$")
FIELD_RE = re.compile(r"^- \*\*([A-Za-z ]+):\*\* *(.*)$")
CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)
TASK_FIELDS = ("Status", "Milestone", "Issue", "Depends on", "Requirements")


@dataclass
class Milestone:
    id: str
    title: str
    due: str = ""
    description: str = ""
    exit_demo: str = ""

    @property
    def gh_title(self) -> str:
        return f"{self.id} · {self.title}"


@dataclass
class Requirement:
    id: str
    priority: str
    traces: list[str]
    text: str
    module_dir: Path


@dataclass
class Task:
    id: str
    code: str
    title: str
    module_dir: Path
    fields: dict[str, str]
    body: str
    line: int

    @property
    def status(self) -> str:
        return self.fields.get("Status", "").strip()

    @property
    def milestone(self) -> str:
        return self.fields.get("Milestone", "").strip()

    @property
    def issue(self) -> int | None:
        m = re.match(r"#(\d+)", self.fields.get("Issue", "").strip())
        return int(m.group(1)) if m else None

    @property
    def deps(self) -> list[str]:
        return _split(self.fields.get("Depends on", ""))

    @property
    def reqs(self) -> list[str]:
        return _split(self.fields.get("Requirements", ""))

    @property
    def done(self) -> bool:
        return self.status == "done"


@dataclass
class Project:
    prd_ids: list[str]
    milestones: dict[str, Milestone]
    modules: list[Path]
    reqs: dict[str, Requirement] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _split(value: str) -> list[str]:
    value = value.strip()
    if value.lower() in NONE:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:  # projects outside the repo (tests)
        return str(p)


# --------------------------------------------------------------------------- parsing


def load(root: Path = ROOT) -> Project:
    specs = root / "specs"
    prd_text = (root / "docs" / "prd.md").read_text()
    prd_ids = list(dict.fromkeys(PRD_ID_RE.findall(prd_text)))
    proj = Project(prd_ids=prd_ids, milestones=_load_milestones(specs / "milestones.md"), modules=[])
    proj.modules = sorted(p for p in specs.iterdir() if p.is_dir() and re.match(r"\d\d-", p.name))
    for mod in proj.modules:
        spec = mod / "spec.md"
        if spec.exists():
            for i, line in enumerate(spec.read_text().splitlines(), 1):
                m = REQ_RE.match(line)
                if m:
                    rid = m.group(1)
                    if rid in proj.reqs:
                        proj.errors.append(f"{_rel(spec)}:{i}: duplicate requirement {rid}")
                    proj.reqs[rid] = Requirement(rid, m.group(2), _split(m.group(3)), m.group(4), mod)
                elif re.match(r"^- \*\*[A-Z]+-R\d+\*\*", line):
                    proj.errors.append(
                        f"{_rel(spec)}:{i}: malformed requirement line (want '- **X-R1** (P0 · F1) — text')"
                    )
        tasks_md = mod / "tasks.md"
        if tasks_md.exists():
            _load_tasks(tasks_md, mod, proj)
    return proj


def _load_milestones(path: Path) -> dict[str, Milestone]:
    out: dict[str, Milestone] = {}
    cur: Milestone | None = None
    desc: list[str] = []
    demo: list[str] | None = None
    for line in path.read_text().splitlines():
        m = MILESTONE_RE.match(line)
        if m:
            if cur:
                cur.description = " ".join(desc).strip()
            cur, desc, demo = Milestone(m.group(1), m.group(2).strip()), [], None
            out[cur.id] = cur
        elif cur and line.startswith("Exit demo:"):
            demo = [line.split("Exit demo:", 1)[1].strip()]
        elif cur and demo is not None and line.strip():
            demo.append(line.strip())
        elif cur and demo is not None:
            cur.exit_demo, demo = " ".join(demo).strip(), None
        elif cur and line.startswith("**Due:**"):
            cur.due = line.split("**Due:**", 1)[1].strip()
        elif cur and line.startswith("**Goal:**"):
            desc.append(line.split("**Goal:**", 1)[1].strip())
    if cur:
        cur.description = " ".join(desc).strip()
        if demo is not None:
            cur.exit_demo = " ".join(demo).strip()
    return out


def _load_tasks(path: Path, mod: Path, proj: Project) -> None:
    lines = path.read_text().splitlines()
    starts = [i for i, ln in enumerate(lines) if TASK_RE.match(ln)]
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        # a following level-2 heading also ends the block
        for j in range(start + 1, end):
            if lines[j].startswith("## "):
                end = j
                break
        m = TASK_RE.match(lines[start])
        assert m
        tid, code, title = m.group(1), m.group(2), m.group(3).strip()
        fields: dict[str, str] = {}
        for ln in lines[start + 1 : end]:
            fm = FIELD_RE.match(ln)
            if fm and fm.group(1) in TASK_FIELDS:
                fields[fm.group(1)] = fm.group(2).strip()
        body = "\n".join(lines[start + 1 : end]).strip()
        if tid in proj.tasks:
            proj.errors.append(f"{_rel(path)}:{start + 1}: duplicate task id {tid}")
        proj.tasks[tid] = Task(tid, code, title, mod, fields, body, start + 1)


# --------------------------------------------------------------------------- validation


def validate(proj: Project) -> list[str]:
    errs = list(proj.errors)
    module_codes: dict[str, Path] = {}
    for t in proj.tasks.values():
        where = f"{_rel(t.module_dir / 'tasks.md')}:{t.line} {t.id}"
        for f in TASK_FIELDS:
            if f not in t.fields:
                errs.append(f"{where}: missing field '{f}'")
        if t.status not in STATUSES:
            errs.append(f"{where}: status '{t.status}' not in {STATUSES}")
        if t.milestone not in proj.milestones:
            errs.append(f"{where}: unknown milestone '{t.milestone}'")
        issue_raw = t.fields.get("Issue", "").strip()
        if issue_raw.lower() not in NONE and t.issue is None:
            errs.append(f"{where}: issue must be '#123' or '—'")
        for d in t.deps:
            if d not in proj.tasks:
                errs.append(f"{where}: unknown dependency {d}")
            elif d == t.id:
                errs.append(f"{where}: depends on itself")
        if not t.reqs:
            errs.append(f"{where}: no requirements listed")
        for r in t.reqs:
            if r not in proj.reqs:
                errs.append(f"{where}: unknown requirement {r}")
        if t.done:
            open_deps = [d for d in t.deps if d in proj.tasks and not proj.tasks[d].done]
            if open_deps:
                errs.append(f"{where}: marked done but dependencies are not: {', '.join(open_deps)}")
        prev = module_codes.setdefault(t.code, t.module_dir)
        if prev != t.module_dir:
            errs.append(f"{where}: task code {t.code} also used in {_rel(prev)}")
    errs += _cycles(proj)

    prd = set(proj.prd_ids)
    covered_reqs = {r for t in proj.tasks.values() for r in t.reqs}
    for r in proj.reqs.values():
        for tr in r.traces:
            if tr not in prd:
                errs.append(f"{_rel(r.module_dir / 'spec.md')}: {r.id} traces unknown PRD id '{tr}'")
        if r.id not in covered_reqs:
            errs.append(f"{_rel(r.module_dir / 'spec.md')}: {r.id} is not implemented by any task")
    traced = {tr for r in proj.reqs.values() for tr in r.traces}
    for pid in proj.prd_ids:
        if pid not in traced:
            errs.append(f"docs/prd.md: {pid} is not traced by any spec requirement")
    for mod in proj.modules:
        for f in ("spec.md", "plan.md", "tasks.md"):
            if not (mod / f).exists():
                errs.append(f"{_rel(mod)}: missing {f}")
    return errs


def _cycles(proj: Project) -> list[str]:
    state: dict[str, int] = {}
    errs: list[str] = []

    def visit(tid: str, path: list[str]) -> None:
        if state.get(tid) == 2:
            return
        if state.get(tid) == 1:
            errs.append("dependency cycle: " + " -> ".join([*path, tid]))
            return
        state[tid] = 1
        for d in proj.tasks[tid].deps:
            if d in proj.tasks:
                visit(d, [*path, tid])
        state[tid] = 2

    for tid in proj.tasks:
        visit(tid, [])
    return errs


# --------------------------------------------------------------------------- reporting


def ready(proj: Project, milestone: str | None = None) -> list[Task]:
    out = [
        t
        for t in proj.tasks.values()
        if not t.done
        and all(proj.tasks[d].done for d in t.deps if d in proj.tasks)
        and (milestone is None or t.milestone == milestone)
    ]
    return sorted(out, key=lambda t: (t.milestone, t.module_dir.name, t.id))


def _bar(done: int, total: int, width: int = 10) -> str:
    if total == 0:
        return "—"
    filled = round(width * done / total)
    return "█" * filled + "░" * (width - filled) + f" {done}/{total}"


def render_status(proj: Project) -> str:
    tasks = sorted(proj.tasks.values(), key=lambda t: (t.module_dir.name, t.id))
    done = sum(t.done for t in tasks)
    out = [
        "# Status",
        "",
        "<!-- GENERATED on main by .github/workflows/status.yml after each merge. Do not edit. -->",
        "",
        f"**Overall:** {_bar(done, len(tasks), 20)} tasks done",
        "",
        "## By milestone",
        "",
        "| Milestone | Due | Progress |",
        "|---|---|---|",
    ]
    for m in proj.milestones.values():
        ms = [t for t in tasks if t.milestone == m.id]
        out.append(f"| {m.gh_title} | {m.due or '—'} | {_bar(sum(t.done for t in ms), len(ms))} |")
    out += ["", "## By module", "", "| Module | Progress |", "|---|---|"]
    for mod in proj.modules:
        ms = [t for t in tasks if t.module_dir == mod]
        out.append(f"| [{mod.name}]({mod.name}/tasks.md) | {_bar(sum(t.done for t in ms), len(ms))} |")
    out += [
        "",
        "## Tasks",
        "",
        "| | Task | Title | Milestone | Depends on | Issue |",
        "|---|---|---|---|---|---|",
    ]
    for t in tasks:
        mark = "✅" if t.done else "⬜"
        issue = f"#{t.issue}" if t.issue else "—"
        deps = ", ".join(t.deps) or "—"
        out.append(f"| {mark} | {t.id} | {t.title} | {t.milestone} | {deps} | {issue} |")
    out += [
        "",
        "## Traceability (PRD → requirements → tasks)",
        "",
        "| PRD | Requirements | Tasks | Done |",
        "|---|---|---|---|",
    ]
    for pid in proj.prd_ids:
        rids = [r.id for r in proj.reqs.values() if pid in r.traces]
        tids = sorted({t.id for t in tasks if set(t.reqs) & set(rids)})
        n_done = sum(proj.tasks[t].done for t in tids)
        out.append(
            f"| {pid} | {', '.join(rids) or '—'} | {', '.join(tids) or '—'} | {_bar(n_done, len(tids), 5)} |"
        )
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- github


def _gh(*args: str, input: str | None = None) -> str:
    for attempt in range(5):
        res = subprocess.run(["gh", *args], input=input, capture_output=True, text=True, cwd=ROOT)
        # "dial tcp" = the connection was never established, so retrying cannot duplicate a write
        if res.returncode == 0 or "dial tcp" not in res.stderr:
            break
        time.sleep(2 * (attempt + 1))
    if res.returncode != 0:
        raise SystemExit(
            f"gh {' '.join(args)} failed:\n{res.stderr}\n"
            "Issue numbers created so far are already written back; re-run to continue (idempotent)."
        )
    return res.stdout


def issue_title(t: Task) -> str:
    return f"[{t.id}] {t.title}"


def issue_body(t: Task, proj: Project) -> str:
    mod = _rel(t.module_dir)
    reqs = "\n".join(
        f"- **{r}** — {proj.reqs[r].text} _(traces {', '.join(proj.reqs[r].traces)})_"
        for r in t.reqs
        if r in proj.reqs
    )
    body = re.sub(r"^- \*\*(Status|Issue):\*\*.*\n?", "", t.body, flags=re.M)
    return (
        f"> Generated from [`{mod}/tasks.md`](../blob/main/{mod}/tasks.md) — edit the markdown, "
        f"not this issue, then run `make issues-sync ARGS=--update`.\n\n"
        f"**Spec:** [`{mod}/spec.md`](../blob/main/{mod}/spec.md) · "
        f"**Plan:** [`{mod}/plan.md`](../blob/main/{mod}/plan.md)\n\n"
        f"{body}\n\n### Requirements\n{reqs}\n\n"
        f"### Definition of done\nSee [AGENTS.md](../blob/main/AGENTS.md#definition-of-done). "
        f"Implemented as one commit on `milestone/{t.milestone}`; that milestone's PR closes this issue.\n"
    )


def sync_issues(proj: Project, dry: bool, update: bool) -> None:
    labels = {"task": ("0e8a16", "A spec task (one issue → one PR)")}
    for t in proj.tasks.values():
        labels[f"module:{t.code.lower()}"] = ("1d76db", f"Module {t.module_dir.name}")
    existing_ms: dict[str, int] = {}
    existing_issues: dict[str, int] = {}
    if not dry:
        have = set(json.loads(_gh("label", "list", "--limit", "200", "--json", "name", "--jq", "[.[].name]")))
        for name, (color, desc) in labels.items():
            if name not in have:
                _gh("label", "create", name, "--color", color, "--description", desc, "--force")
        for m in json.loads(_gh("api", "repos/{owner}/{repo}/milestones?state=all&per_page=100")):
            existing_ms[m["title"]] = m["number"]
        for i in json.loads(
            _gh(
                "issue",
                "list",
                "--label",
                "task",
                "--state",
                "all",
                "--limit",
                "1000",
                "--json",
                "number,title",
            )
        ):
            m = re.match(r"\[(T-[A-Z]+-\d{2})\]", i["title"])
            if m:
                existing_issues[m.group(1)] = i["number"]
    for m in proj.milestones.values():
        if m.gh_title in existing_ms:
            continue
        print(f"milestone  + {m.gh_title}")
        if not dry:
            args = ["api", "-X", "POST", "repos/{owner}/{repo}/milestones", "-f", f"title={m.gh_title}"]
            args += ["-f", f"description={m.description}"]
            if m.due:
                args += ["-f", f"due_on={m.due}T23:59:59Z"]
            _gh(*args)
    written: dict[str, int] = {}
    for t in sorted(proj.tasks.values(), key=lambda t: (t.milestone, t.id)):
        labels_arg = f"task,module:{t.code.lower()}"
        number = t.issue or existing_issues.get(t.id)
        if number:
            if update and not dry:
                _gh(
                    "issue",
                    "edit",
                    str(number),
                    "--title",
                    issue_title(t),
                    "--body-file",
                    "-",
                    input=issue_body(t, proj),
                )
                print(f"issue      ~ #{number} {t.id}")
            if not t.issue:
                _write_issue(t, number)
                written[t.id] = number
            continue
        if t.done:
            continue  # done before issues existed (e.g. bootstrap) — nothing to track
        print(f"issue      + {issue_title(t)}")
        if dry:
            continue
        url = _gh(
            "issue",
            "create",
            "--title",
            issue_title(t),
            "--body-file",
            "-",
            "--label",
            labels_arg,
            "--milestone",
            proj.milestones[t.milestone].gh_title,
            input=issue_body(t, proj),
        ).strip()
        number = int(url.rsplit("/", 1)[1])
        _write_issue(t, number)  # immediately, so an interrupted run loses nothing (re-run is idempotent)
        written[t.id] = number
    if written:
        print(f"wrote {len(written)} issue numbers back into tasks.md — commit them.")


def _write_issue(t: Task, number: int) -> None:
    path = t.module_dir / "tasks.md"
    lines = path.read_text().splitlines(keepends=True)
    for i in range(t.line, len(lines)):
        if lines[i].startswith("- **Issue:**"):
            lines[i] = f"- **Issue:** #{number}\n"
            break
        if TASK_RE.match(lines[i]):
            break
    path.write_text("".join(lines))


MILESTONE_BRANCH_RE = re.compile(r"^milestone/(M\d+)$")


def milestone_tasks(proj: Project, milestone: str) -> list[Task]:
    return sorted((t for t in proj.tasks.values() if t.milestone == milestone), key=lambda t: t.id)


def pr_body(proj: Project, milestone: str) -> str:
    """The milestone PR body: every task as a checkbox, plus `Closes #N` for each task that is done."""
    m = proj.milestones[milestone]
    ts = milestone_tasks(proj, milestone)
    done = [t for t in ts if t.done]
    lines = [
        f"## [{m.id}] {m.title}",
        "",
        "<!-- GENERATED by `make milestone-pr M="
        + m.id
        + "` from specs/*/tasks.md; do not edit by hand. -->",
        "",
        f"**Goal:** {m.description}",
        "",
    ]
    if m.exit_demo:
        lines += [f"**Exit demo:** {m.exit_demo}", ""]
    lines += [f"### Tasks ({len(done)}/{len(ts)} done)", ""]
    for t in ts:
        issue = f" (#{t.issue})" if t.issue else ""
        lines.append(f"- [{'x' if t.done else ' '}] {t.id} · {t.title}{issue}")
    lines += [
        "",
        "One commit per task (`feat(<module>): … (T-XXX-NN)`). Review commit by commit; this PR stays a",
        "draft until every task is done, then it is marked ready and merged as a whole.",
        "",
        "### Closes",
        "",
    ]
    lines += [f"Closes #{t.issue}" for t in done if t.issue] or ["(no task done yet)"]
    lines += [
        "",
        "### Definition of done (per task, see AGENTS.md)",
        "- Tests listed in the task exist and pass; `make check` is green",
        "- Status `done` and acceptance criteria ticked in `tasks.md`; spec/plan updated if reality differed",
        "- No secrets or real PII; new config in `.env.example`; new dependencies named in `plan.md`",
        "- `specs/STATUS.md` not edited (regenerated on main after merge)",
        "",
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    ]
    return "\n".join(lines) + "\n"


def check_pr(proj: Project, body: str, branch: str = "", ready: bool = False) -> list[str]:
    """Enforce one PR per milestone.

    - `milestone/Mn`: the body must close exactly the done tasks of Mn (regenerate it with
      `make milestone-pr M=Mn`); a PR marked ready (not draft) needs every task of Mn done.
    - `task/*` branches are rejected: task work goes onto its milestone branch.
    - any other branch (chore/…) may not close task issues.
    """
    by_issue = {t.issue: t for t in proj.tasks.values() if t.issue}
    closes = {int(n) for n in CLOSES_RE.findall(body)}
    if branch.startswith("task/"):
        return [f"one PR per milestone: push task commits to milestone/<Mn>, not {branch}"]
    m = MILESTONE_BRANCH_RE.match(branch)
    if not m:
        return [
            f"#{n} ({by_issue[n].id}) is a task issue; only its milestone PR may close it"
            for n in sorted(closes)
            if n in by_issue
        ]
    ms = m.group(1)
    if ms not in proj.milestones:
        return [f"unknown milestone {ms} in branch {branch}"]
    ts = milestone_tasks(proj, ms)
    expected = {t.issue for t in ts if t.done and t.issue}
    errs = []
    for n in sorted(expected - closes):
        errs.append(
            f"body is missing 'Closes #{n}' ({by_issue[n].id} is done) — run `make milestone-pr M={ms}`"
        )
    for n in sorted(closes - expected):
        t = by_issue.get(n)
        if t is None:
            continue
        if t.milestone != ms:
            errs.append(f"#{n} ({t.id}) belongs to {t.milestone}, not {ms}")
        else:
            errs.append(
                f"PR closes #{n} ({t.id}) but its Status is not 'done' in {_rel(t.module_dir)}/tasks.md"
            )
    if ready:
        todo = [t.id for t in ts if not t.done]
        if todo:
            errs.append(
                f"{ms} PR is marked ready but tasks are not done: {', '.join(todo)} (keep it a draft)"
            )
    return errs


def milestone_pr(proj: Project, milestone: str) -> None:
    branch = f"milestone/{milestone}"
    body = pr_body(proj, milestone)
    m = proj.milestones[milestone]
    found = json.loads(_gh("pr", "list", "--head", branch, "--state", "open", "--json", "number"))
    if found:
        number = str(found[0]["number"])
        _gh("pr", "edit", number, "--body-file", "-", input=body)
        print(f"updated PR #{number}")
    else:
        url = _gh(
            "pr", "create", "--draft", "--base", "main", "--head", branch,
            "--title", f"[{m.id}] {m.title}", "--milestone", m.gh_title, "--body-file", "-",
            input=body,
        ).strip()  # fmt: skip
        print(f"created draft PR {url}")


# --------------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    st = sub.add_parser("status")
    st.add_argument("--write", action="store_true")
    st.add_argument("--check", action="store_true")
    nx = sub.add_parser("next")
    nx.add_argument("--milestone")
    sh = sub.add_parser("show")
    sh.add_argument("task_id")
    sy = sub.add_parser("sync-issues")
    sy.add_argument("--dry-run", action="store_true")
    sy.add_argument("--update", action="store_true", help="also rewrite bodies of existing issues")
    cp = sub.add_parser("check-pr")
    cp.add_argument("--body-file", required=True)
    cp.add_argument("--branch", default="", help="PR head branch, e.g. milestone/M1")
    cp.add_argument("--ready", action="store_true", help="the PR is not a draft")
    pb = sub.add_parser("pr-body")
    pb.add_argument("milestone")
    mp = sub.add_parser("milestone-pr")
    mp.add_argument("milestone")
    args = ap.parse_args(argv)

    proj = load()
    if args.cmd == "validate":
        errs = validate(proj)
        for e in errs:
            print(f"error: {e}")
        n_done = sum(t.done for t in proj.tasks.values())
        print(f"{len(proj.reqs)} requirements, {len(proj.tasks)} tasks ({n_done} done), {len(errs)} errors")
        return 1 if errs else 0
    if args.cmd == "status":
        text = render_status(proj)
        if args.write:
            STATUS_FILE.write_text(text)
            print(f"wrote {_rel(STATUS_FILE)}")
        elif args.check:
            if not STATUS_FILE.exists() or STATUS_FILE.read_text() != text:
                print(
                    "specs/STATUS.md is stale — run `python3 scripts/tasks.py status --write`",
                    file=sys.stderr,
                )
                return 1
            print("specs/STATUS.md is up to date")
        else:
            print(text)
        return 0
    if args.cmd == "next":
        items = ready(proj, args.milestone)
        if not items:
            print("no ready tasks")
        for t in items:
            issue = f"#{t.issue}" if t.issue else "(no issue)"
            print(f"{t.milestone}  {t.id:<12} {issue:<10} {t.title}")
        return 0
    if args.cmd == "show":
        t = proj.tasks.get(args.task_id)
        if not t:
            print(f"unknown task {args.task_id}", file=sys.stderr)
            return 1
        mod = _rel(t.module_dir)
        print(
            f"### {t.id} · {t.title}\n(spec: {mod}/spec.md · plan: {mod}/plan.md · tasks: {mod}/tasks.md)\n"
        )
        print(t.body)
        for r in t.reqs:
            if r in proj.reqs:
                print(f"\n{r}: {proj.reqs[r].text}")
        return 0
    if args.cmd == "sync-issues":
        errs = validate(proj)
        if errs:
            print("fix `validate` errors before syncing", file=sys.stderr)
            return 1
        sync_issues(proj, args.dry_run, args.update)
        return 0
    if args.cmd in ("pr-body", "milestone-pr") and args.milestone not in proj.milestones:
        print(f"unknown milestone {args.milestone}", file=sys.stderr)
        return 1
    if args.cmd == "pr-body":
        print(pr_body(proj, args.milestone), end="")
        return 0
    if args.cmd == "milestone-pr":
        errs = validate(proj)
        if errs:
            print("fix `validate` errors first", file=sys.stderr)
            return 1
        milestone_pr(proj, args.milestone)
        return 0
    if args.cmd == "check-pr":
        errs = check_pr(proj, Path(args.body_file).read_text(), args.branch, args.ready)
        for e in errs:
            print(f"error: {e}")
        return 1 if errs else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
