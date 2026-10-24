"""Tests for the Business Q&A slice: read only enforcement, evidence
format, fallback behavior, and end to end demo questions."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from personal_assistant_agent.orchestrator import (
    UNKNOWN_ANSWER,
    AnswerWithEvidence,
    BusinessQueryInput,
    PermissionLevel,
    TenantContext,
    answer_business_question,
    registry_with_business_qa,
    seed_demo_store,
    validate_readonly_sql,
)

CTX = TenantContext(user_id="user-1", workspace_id="ws-1")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def store():
    return seed_demo_store()


def sales_count(store):
    return store.query("SELECT COUNT(*) AS n FROM sales")[0]["n"]


WRITE_STATEMENTS = [
    "INSERT INTO sales VALUES (99, 'x', 'y', 'z', 1, 1, '2026-01-01')",
    "UPDATE sales SET quantity = 0",
    "DELETE FROM sales",
    "DROP TABLE sales",
    "drop table sales",
    "ALTER TABLE sales ADD COLUMN note TEXT",
    "CREATE TABLE evil (id INTEGER)",
    "TRUNCATE TABLE sales",
    "SELECT * FROM sales; DROP TABLE sales",
    "SELECT * FROM sales; DELETE FROM sales",
    "-- just a comment\nDELETE FROM sales",
    "/* hidden */ DROP TABLE inventory",
    "VACUUM",
    "PRAGMA query_only = OFF",
]


@pytest.mark.parametrize("sql", WRITE_STATEMENTS)
def test_write_statements_rejected(sql):
    with pytest.raises(ValueError):
        validate_readonly_sql(sql)


@pytest.mark.parametrize("sql", WRITE_STATEMENTS)
def test_write_attempts_rejected_by_store_and_leave_data_untouched(sql, store):
    before = sales_count(store)
    with pytest.raises(ValueError):
        store.query(sql)
    assert sales_count(store) == before


def test_select_and_with_queries_allowed(store):
    assert validate_readonly_sql("SELECT * FROM sales;").startswith("SELECT")
    assert validate_readonly_sql(
        "WITH t AS (SELECT 1 AS x) SELECT * FROM t"
    ).startswith("WITH")
    rows = store.query("SELECT product FROM sales LIMIT 2", limit=2)
    assert len(rows) == 2


def test_tool_registered_with_read_permission(store):
    registry = registry_with_business_qa(store)
    tool = registry.get("query_business_data")
    assert tool.permission == PermissionLevel.READ
    args = tool.validate({"sql": "SELECT COUNT(*) AS n FROM sales"})
    assert isinstance(args, BusinessQueryInput)
    result = tool.handler(args, CTX)
    assert result == [{"n": 7}]


def test_tool_rejects_write_input(store):
    registry = registry_with_business_qa(store)
    tool = registry.get("query_business_data")
    args = tool.validate({"sql": "DELETE FROM sales"})
    with pytest.raises(ValueError):
        tool.handler(args, CTX)


def test_evidence_format_has_exactly_three_parts(store):
    result = answer_business_question("مبيعات الشهر ده كام؟", store)
    rendered = result.render()
    sections = ["الإجابة:", "الاستعلام:", "الدليل:"]
    positions = [rendered.index(section) for section in sections]
    assert positions == sorted(positions)
    assert rendered.count("الإجابة:") == 1
    assert rendered.count("الاستعلام:") == 1
    assert rendered.count("الدليل:") == 1
    assert "10700" in rendered


def test_evidence_summarizes_many_rows():
    rows = [{"product": f"item-{i}"} for i in range(25)]
    rendered = AnswerWithEvidence(answer="test", query="SELECT 1", rows=rows).render(
        max_rows=2
    )
    assert "25 صف" in rendered
    assert "item-0" in rendered
    assert "item-24" not in rendered


def test_fallback_for_unknown_question_invents_no_number(store):
    result = answer_business_question("إيه حالة الطقس بكرة؟", store)
    assert result.known is False
    assert result.answer == UNKNOWN_ANSWER
    assert result.rows == []
    assert result.query == ""
    assert not re.search(r"\d", result.answer)


def test_fallback_when_query_returns_nothing(store):
    store.connection.execute("PRAGMA query_only = OFF")
    store.connection.execute("DELETE FROM inventory")
    store.connection.commit()
    store.connection.execute("PRAGMA query_only = ON")
    result = answer_business_question("إيه المنتجات اللي مخزونها هيخلص؟", store)
    assert result.known is False
    assert result.answer == UNKNOWN_ANSWER


def test_end_to_end_demo_questions(store):
    month = answer_business_question("مبيعات الشهر ده كام؟", store)
    assert month.known and "10700" in month.answer

    top = answer_business_question("مين أكتر عميل اشترى؟", store)
    assert top.known and "أحمد" in top.answer and "4500" in top.answer

    low = answer_business_question("إيه المنتجات اللي مخزونها هيخلص؟", store)
    assert low.known
    assert "سماعات لاسلكية" in low.answer
    assert "باور بانك" in low.answer

    branch = answer_business_question("مبيعات فرع القاهرة كام؟", store)
    assert branch.known and "8100" in branch.answer


def test_demo_script_runs_end_to_end():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "business_qa_demo.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    for question in (
        "مبيعات الشهر ده كام؟",
        "مين أكتر عميل اشترى؟",
        "إيه المنتجات اللي مخزونها هيخلص؟",
        "مبيعات فرع القاهرة كام؟",
    ):
        assert question in proc.stdout
    assert "الإجابة:" in proc.stdout
    assert "الاستعلام:" in proc.stdout
    assert "الدليل:" in proc.stdout
