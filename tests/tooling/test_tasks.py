"""Tests for scripts/tasks.py — the spec/task traceability tooling."""

from pathlib import Path

import tasks  # scripts/ is on pythonpath (pyproject)

PRD = "# PRD\n| **F1** | voice |\n| **U1** | reminder |\n"
MILESTONES = "## M1 · First\n**Due:** 2026-11-13\n**Goal:** Do things.\n"
SPEC = "# spec\n- **DEMO-R1** (P0 · F1, U1) — The demo shall work.\n"


def _task(tid: str, status: str = "todo", deps: str = "—", reqs: str = "DEMO-R1", issue: str = "—") -> str:
    return (
        f"### {tid} · Title for {tid}\n- **Status:** {status}\n- **Milestone:** M1\n"
        f"- **Issue:** {issue}\n- **Depends on:** {deps}\n- **Requirements:** {reqs}\n\nBody.\n\n"
    )


def _project(tmp_path: Path, tasks_md: str, spec: str = SPEC) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "prd.md").write_text(PRD)
    mod = tmp_path / "specs" / "01-demo"
    mod.mkdir(parents=True)
    (tmp_path / "specs" / "milestones.md").write_text(MILESTONES)
    (mod / "spec.md").write_text(spec)
    (mod / "plan.md").write_text("# plan\n")
    (mod / "tasks.md").write_text("# tasks\n\n## Tasks\n\n" + tasks_md)
    return tmp_path


def _errors(root: Path) -> list[str]:
    return tasks.validate(tasks.load(root))


def test_valid_project(tmp_path: Path) -> None:
    root = _project(tmp_path, _task("T-DEMO-01", "done") + _task("T-DEMO-02", deps="T-DEMO-01"))
    assert _errors(root) == []
    proj = tasks.load(root)
    assert [t.id for t in tasks.ready(proj)] == ["T-DEMO-02"]
    assert proj.milestones["M1"].due == "2026-11-13"


def test_unknown_dependency_and_status(tmp_path: Path) -> None:
    root = _project(tmp_path, _task("T-DEMO-01", status="wip", deps="T-NOPE-01"))
    errs = " ".join(_errors(root))
    assert "unknown dependency T-NOPE-01" in errs
    assert "status 'wip'" in errs


def test_done_requires_done_deps(tmp_path: Path) -> None:
    root = _project(tmp_path, _task("T-DEMO-01") + _task("T-DEMO-02", "done", deps="T-DEMO-01"))
    assert any("dependencies are not" in e for e in _errors(root))


def test_traceability_gaps(tmp_path: Path) -> None:
    spec = SPEC + "- **DEMO-R2** (P1 · F99) — Orphan.\n"
    root = _project(tmp_path, _task("T-DEMO-01"), spec=spec)
    errs = " ".join(_errors(root))
    assert "DEMO-R2 is not implemented by any task" in errs
    assert "unknown PRD id 'F99'" in errs


def test_prd_id_not_traced(tmp_path: Path) -> None:
    spec = "- **DEMO-R1** (P0 · F1) — Only F1.\n"
    root = _project(tmp_path, _task("T-DEMO-01"), spec=spec)
    assert any("U1 is not traced" in e for e in _errors(root))


def test_cycle_detected(tmp_path: Path) -> None:
    root = _project(tmp_path, _task("T-DEMO-01", deps="T-DEMO-02") + _task("T-DEMO-02", deps="T-DEMO-01"))
    assert any("cycle" in e for e in _errors(root))


def test_check_pr(tmp_path: Path) -> None:
    root = _project(tmp_path, _task("T-DEMO-01", issue="#7") + _task("T-DEMO-02", "done", issue="#8"))
    proj = tasks.load(root)
    assert tasks.check_pr(proj, "Closes #8") == []
    assert tasks.check_pr(proj, "fixes #7")  # not marked done → error


def test_write_issue_number(tmp_path: Path) -> None:
    root = _project(tmp_path, _task("T-DEMO-01") + _task("T-DEMO-02"))
    proj = tasks.load(root)
    tasks._write_issue(proj.tasks["T-DEMO-02"], 42)
    proj = tasks.load(root)
    assert proj.tasks["T-DEMO-02"].issue == 42
    assert proj.tasks["T-DEMO-01"].issue is None


def test_repository_specs_are_valid() -> None:
    """The real specs/ tree must always validate (same check as CI's specs job)."""
    assert tasks.validate(tasks.load()) == []
