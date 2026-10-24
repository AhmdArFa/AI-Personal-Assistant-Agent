"""Business Q&A demo: Egyptian Arabic questions, answered with evidence.

    python examples/business_qa_demo.py

Runs four demo questions end to end over seeded demo data (no real
business data): total sales this month, top customer, low stock
items, and branch sales. Every answer prints its three parts:
the short answer, the query, and the source rows.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from personal_assistant_agent.orchestrator import (
    answer_business_question,
    seed_demo_store,
)

QUESTIONS = [
    "مبيعات الشهر ده كام؟",
    "مين أكتر عميل اشترى؟",
    "إيه المنتجات اللي مخزونها هيخلص؟",
    "مبيعات فرع القاهرة كام؟",
]


def main() -> None:
    store = seed_demo_store()
    for question in QUESTIONS:
        result = answer_business_question(question, store)
        print(f"\n=== {question} ===")
        print(result.render())


if __name__ == "__main__":
    main()
