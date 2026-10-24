"""The evaluation suite is a release gate: CI runs it, and a
regression in any task fails the build (work package W6)."""

from evals.runner import run_all
from evals.tasks import TASKS, EvalTask

REQUIRED_CATEGORIES = {
    "arabic",
    "english",
    "mixed",
    "document_lookup",
    "ambiguous",
    "tool_failure",
    "injection",
    "cross_tenant",
    "approval_required",
    "invalid_tool_arguments",
}


def test_suite_covers_required_categories():
    categories = {task.category for task in TASKS}
    assert REQUIRED_CATEGORIES <= categories
    assert 10 <= len(TASKS) <= 20  # grows toward 50 to 100 over time


def test_suite_all_pass():
    report = run_all()
    failures = [r for r in report.results if not r.passed]
    assert report.all_passed, f"eval failures: {failures}"


def test_a_regression_fails_the_report():
    broken = EvalTask("broken", "arabic", lambda: (False, "forced failure"))
    report = run_all([broken])
    assert not report.all_passed
    assert report.results[0].detail == "forced failure"
