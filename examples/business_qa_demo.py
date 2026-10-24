"""Business Q&A demo: Egyptian Arabic questions, answered with evidence.

    python examples/business_qa_demo.py

Runs demo questions end to end over seeded demo data (no real
business data). First as the owner, who sees everything. Then as a
Cairo branch employee: a same branch question is answered with
evidence, and another branch question is refused with no rows.
Every question, answer, and refusal is recorded in the audit log,
which is printed at the end.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from personal_assistant_agent.orchestrator import (
    BusinessAuditLog,
    BusinessScope,
    answer_business_question,
    seed_demo_store,
)

OWNER_QUESTIONS = [
    "مبيعات الشهر ده كام؟",
    "مين أكتر عميل اشترى؟",
    "إيه المنتجات اللي مخزونها هيخلص؟",
    "مبيعات فرع القاهرة كام؟",
]

EMPLOYEE_QUESTIONS = [
    "مبيعات فرع القاهرة كام؟",
    "مبيعات فرع الجيزة كام؟",
]


def main() -> None:
    store = seed_demo_store()
    audit = BusinessAuditLog()

    print("=== صاحب البيزنس (كل الفروع) ===")
    for question in OWNER_QUESTIONS:
        result = answer_business_question(question, store, audit=audit)
        print(f"\n--- {question} ---")
        print(result.render())

    employee = BusinessScope(role="employee", branches=("القاهرة",))
    print("\n\n=== موظف فرع القاهرة (نطاقه فرعه بس) ===")
    for question in EMPLOYEE_QUESTIONS:
        result = answer_business_question(question, store, scope=employee, audit=audit)
        print(f"\n--- {question} ---")
        print(result.render())

    print("\n\n=== سجل التدقيق ===")
    print(audit.render())


if __name__ == "__main__":
    main()
