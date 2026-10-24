"""Evaluation runner: executes tasks, returns a per-task report."""

from __future__ import annotations

from dataclasses import dataclass, field

from .tasks import TASKS, EvalTask


@dataclass
class EvalResult:
    name: str
    category: str
    passed: bool
    detail: str


@dataclass
class EvalReport:
    results: list[EvalResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(result.passed for result in self.results)

    def lines(self) -> list[str]:
        return [
            f"{'PASS' if r.passed else 'FAIL'} [{r.category}] {r.name}: {r.detail}"
            for r in self.results
        ]


def run_all(tasks: list[EvalTask] | None = None) -> EvalReport:
    report = EvalReport()
    for task in tasks if tasks is not None else TASKS:
        try:
            passed, detail = task.run()
        except Exception as exc:  # noqa: BLE001, a crashing task is a failed task
            passed, detail = False, f"raised {type(exc).__name__}: {exc}"
        report.results.append(EvalResult(task.name, task.category, passed, detail))
    return report
