"""Tests for the Business Q&A permissions model: owner vs employee
scope, refusals with no leaked data, the audit trail, and the
combined filter fix from the slice 1 review."""

import pytest

from personal_assistant_agent.orchestrator import (
    BusinessAuditLog,
    BusinessScope,
    answer_business_question,
    registry_with_business_qa,
    seed_demo_store,
)


@pytest.fixture
def store():
    return seed_demo_store()


@pytest.fixture
def cairo_employee():
    return BusinessScope(role="employee", branches=("القاهرة",))


def test_invalid_role_rejected():
    with pytest.raises(ValueError):
        BusinessScope(role="admin")


def test_owner_sees_every_branch(store):
    giza = answer_business_question("مبيعات فرع الجيزة كام؟", store)
    assert giza.known and not giza.refused and "2800" in giza.answer
    month = answer_business_question("مبيعات الشهر ده كام؟", store)
    assert month.known and "10700" in month.answer


def test_employee_same_branch_answered_with_evidence(store, cairo_employee):
    result = answer_business_question(
        "مبيعات فرع القاهرة كام؟", store, scope=cairo_employee
    )
    assert result.known and not result.refused
    assert "8100" in result.answer
    assert result.query != ""
    assert result.rows == [{"total": 8100.0}]


def test_employee_other_branch_refused_with_no_data_leaked(store, cairo_employee):
    result = answer_business_question(
        "مبيعات فرع الجيزة كام؟", store, scope=cairo_employee
    )
    assert result.refused
    assert result.rows == []
    assert result.query == ""
    rendered = result.render()
    assert "مرفوض" in rendered
    assert "2800" not in rendered
    assert "الدليل: لا يوجد." in rendered


def test_employee_unscoped_questions_refused_not_partially_answered(
    store, cairo_employee
):
    for question in (
        "مبيعات كلها كام؟",
        "مبيعات الشهر ده كام؟",
        "مين أكتر عميل اشترى؟",
        "إيه المنتجات اللي مخزونها هيخلص؟",
    ):
        result = answer_business_question(question, store, scope=cairo_employee)
        assert result.refused, question
        assert result.rows == []


def test_scope_bypass_attempts_through_wording_fail(store, cairo_employee):
    for question in (
        "مبيعات فرع القاهرة والجيزة كام؟",
        "الجيزة مبيعاتها كام؟",
        "مبيعات كل الفروع كام؟",
        "مبيعات فرع الجيزه كام؟",
    ):
        result = answer_business_question(question, store, scope=cairo_employee)
        assert result.refused, question
        assert result.rows == []
        assert "2800" not in result.render()


def test_employee_scoped_top_customer_and_low_stock(store, cairo_employee):
    top = answer_business_question(
        "مين أكتر عميل اشترى في فرع القاهرة؟", store, scope=cairo_employee
    )
    assert top.known and "أحمد" in top.answer and "3000" in top.answer
    low = answer_business_question(
        "إيه المنتجات اللي مخزونها هيخلص في فرع القاهرة؟",
        store,
        scope=cairo_employee,
    )
    assert low.known
    assert "سماعات لاسلكية" in low.answer
    assert "باور بانك" in low.answer
    assert all(row["branch"] == "القاهرة" for row in low.rows)


def test_combined_filters_are_applied_not_dropped(store):
    branch_product = answer_business_question(
        "مبيعات سماعات لاسلكية في فرع القاهرة كام؟", store
    )
    assert branch_product.known and "3000" in branch_product.answer
    branch_month = answer_business_question("مبيعات فرع القاهرة الشهر ده كام؟", store)
    assert branch_month.known and "7200" in branch_month.answer
    product_only = answer_business_question("مبيعات شاحن سريع كام؟", store)
    assert product_only.known and "2800" in product_only.answer
    all_sales = answer_business_question("مبيعات كلها كام؟", store)
    assert all_sales.known and "12400" in all_sales.answer


def test_tool_filters_employee_rows_and_refuses_pure_aggregates(store, cairo_employee):
    registry = registry_with_business_qa(store, scope=cairo_employee)
    tool = registry.get("query_business_data")

    grouped = tool.handler(
        tool.validate(
            {
                "sql": "SELECT branch, SUM(quantity * unit_price) AS total"
                " FROM sales GROUP BY branch"
            }
        ),
        None,
    )
    assert [row["branch"] for row in grouped] == ["القاهرة"]

    rows = tool.handler(
        tool.validate({"sql": "SELECT * FROM sales"}),
        None,
    )
    assert rows and all(row["branch"] == "القاهرة" for row in rows)

    with pytest.raises(PermissionError):
        tool.handler(
            tool.validate(
                {"sql": "SELECT SUM(quantity * unit_price) AS total FROM sales"}
            ),
            None,
        )


def test_tool_scope_does_not_weaken_read_only(store, cairo_employee):
    registry = registry_with_business_qa(store, scope=cairo_employee)
    tool = registry.get("query_business_data")
    with pytest.raises(ValueError):
        tool.handler(tool.validate({"sql": "DELETE FROM sales"}), None)


def test_audit_records_answers_and_refusals(store, cairo_employee):
    audit = BusinessAuditLog()
    answer_business_question(
        "مبيعات فرع القاهرة كام؟", store, scope=cairo_employee, audit=audit
    )
    answer_business_question(
        "مبيعات فرع الجيزة كام؟", store, scope=cairo_employee, audit=audit
    )
    answer_business_question("إيه حالة الطقس بكرة؟", store, audit=audit)
    assert [entry.outcome for entry in audit.entries] == [
        "answered",
        "refused",
        "unknown",
    ]
    answered, refused, _unknown = audit.entries
    assert answered.query != "" and answered.rows_count >= 1
    assert refused.query == "" and refused.rows_count == 0
    assert refused.role == "employee"
    assert refused.scope_branches == ("القاهرة",)
    rendered = audit.render()
    assert "answered" in rendered and "refused" in rendered


def test_tool_audit_records_refusal(store, cairo_employee):
    audit = BusinessAuditLog()
    registry = registry_with_business_qa(store, scope=cairo_employee, audit=audit)
    tool = registry.get("query_business_data")
    with pytest.raises(PermissionError):
        tool.handler(
            tool.validate(
                {"sql": "SELECT SUM(quantity * unit_price) AS total FROM sales"}
            ),
            None,
        )
    assert audit.entries[-1].outcome == "refused"
