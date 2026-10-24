"""Run the evaluation suite: python -m evals"""

import sys

from .runner import run_all


def main() -> int:
    report = run_all()
    for line in report.lines():
        print(line)
    passed = sum(r.passed for r in report.results)
    print(f"{passed}/{len(report.results)} passed")
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
