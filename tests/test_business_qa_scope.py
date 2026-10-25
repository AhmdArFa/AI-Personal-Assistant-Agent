"""Tests for the Business Q&A company data scope: the allowlist of
tables and columns a company approves, enforced before execution.

Anything outside the list is unreachable: unknown tables, unlisted
columns, and star selects over tables with unlisted columns are all
rejected, at the store, at the tool (as a recorded refusal), and in
the demo answerer. The scope composes with the role scope: both
walls apply.
"""

import pytest

from personal_assistant_agent.orchestrator import (
    BusinessAuditLog,
    BusinessScope,
    CompanyDataScope,
    CompanyScopeError,
    answer_business_question,
    registry_with_business_qa,
    seed_demo_store,
    validate_company_scope,
)

FULL_SALES = (
    "id",
    "product",
    "customer",
    "branch",
    "quantity",
    "unit_price",
    "sale_date",
)
SALES_NO_CUSTOMER = tuple(c for c in FULL_SALES if c != "customer")
FULL_INVENTORY = (
    "id",
    "product",
    "branch",
    "quantity_on_hand",
    "reorder_level",
)


@pytest.fixture
def sales_only_store():
    return seed_demo_store(
        company_scope=CompanyDataScope(
            allowed={"sales": FULL_SALES},
        )
    )


@pytest.fixture
def no_customer_store():
    return seed_demo_store(
        company_scope=CompanyDataScope(
            allowed={"sales": SALES_NO_CUSTOMER, "inventory": FULL_INVENTORY},
        )
    )


def test_demo_default_allows_demo_queries():
    store = seed_demo_store()
    assert store.query("SELECT * FROM sales")
    assert store.query("SELECT * FROM inventory")


def test_scope_keys_are_case_insensitive():
    scope = CompanyDataScope(allowed={"SALES": ("PRODUCT", "BRANCH")})
    assert scope.allows_table("sales")
    assert scope.allows_column("Sales", "product")
    assert not scope.allows_column("sales", "customer")


def test_unknown_table_rejected(sales_only_store):
    with pytest.raises(CompanyScopeError, match="inventory"):
        sales_only_store.query("SELECT * FROM inventory")
    assert sales_only_store.query("SELECT product FROM sales")


def test_unlisted_column_rejected(no_customer_store):
    with pytest.raises(CompanyScopeError, match="customer"):
        no_customer_store.query("SELECT customer FROM sales")
    narrow = seed_demo_store(
        company_scope=CompanyDataScope(
            allowed={"sales": tuple(c for c in FULL_SALES if c != "quantity")},
        )
    )
    with pytest.raises(CompanyScopeError, match="quantity"):
        narrow.query("SELECT SUM(quantity * unit_price) AS total FROM sales")
    assert no_customer_store.query("SELECT product, branch FROM sales")


def test_star_rejected_when_columns_unlisted(no_customer_store):
    with pytest.raises(CompanyScopeError, match="sales"):
        no_customer_store.query("SELECT * FROM sales")
    with pytest.raises(CompanyScopeError, match="sales"):
        no_customer_store.query("SELECT sales.* FROM sales")
    assert no_customer_store.query("SELECT * FROM inventory")


def test_star_allowed_when_table_fully_allowed():
    store = seed_demo_store()
    assert store.query("SELECT * FROM sales")
    assert store.query("SELECT sales.* FROM sales")


def test_join_qualified_columns(sales_only_store):
    store = seed_demo_store(
        company_scope=CompanyDataScope(
            allowed={"sales": FULL_SALES, "inventory": FULL_INVENTORY},
        )
    )
    rows = store.query(
        "SELECT sales.product, inventory.quantity_on_hand"
        " FROM sales JOIN inventory ON sales.product = inventory.product"
    )
    assert rows

    narrow = seed_demo_store(
        company_scope=CompanyDataScope(
            allowed={
                "sales": FULL_SALES,
                "inventory": tuple(
                    c for c in FULL_INVENTORY if c != "quantity_on_hand"
                ),
            },
        )
    )
    with pytest.raises(CompanyScopeError, match="quantity_on_hand"):
        narrow.query(
            "SELECT sales.product, inventory.quantity_on_hand"
            " FROM sales JOIN inventory ON sales.product = inventory.product"
        )


def test_unqualified_column_in_multi_table_query_rejected():
    store = seed_demo_store(
        company_scope=CompanyDataScope(
            allowed={"sales": FULL_SALES, "inventory": FULL_INVENTORY},
        )
    )
    with pytest.raises(CompanyScopeError, match="unqualified"):
        store.query(
            "SELECT product FROM sales"
            " JOIN inventory ON sales.product = inventory.product"
        )


def test_comma_join_tables_checked(sales_only_store):
    with pytest.raises(CompanyScopeError, match="inventory"):
        sales_only_store.query(
            "SELECT sales.product, inventory.quantity_on_hand FROM sales, inventory"
        )


def test_cte_body_columns_checked(no_customer_store):
    with pytest.raises(CompanyScopeError, match="customer"):
        no_customer_store.query(
            "WITH top AS (SELECT customer FROM sales) SELECT customer FROM top"
        )
    rows = no_customer_store.query(
        "WITH top AS (SELECT product FROM sales) SELECT product FROM top"
    )
    assert rows


def test_cte_over_disallowed_table_rejected(sales_only_store):
    with pytest.raises(CompanyScopeError, match="inventory"):
        sales_only_store.query(
            "WITH low AS (SELECT product FROM inventory) SELECT product FROM low"
        )


def test_validate_company_scope_standalone():
    scope = CompanyDataScope(allowed={"sales": FULL_SALES})
    assert (
        validate_company_scope("SELECT product FROM sales", scope, None)
        == "SELECT product FROM sales"
    )
    with pytest.raises(CompanyScopeError):
        validate_company_scope("SELECT * FROM sales", scope, None)


def test_tool_refusal_is_audited(no_customer_store):
    audit = BusinessAuditLog()
    registry = registry_with_business_qa(no_customer_store, audit=audit)
    tool = registry.get("query_business_data")
    with pytest.raises(PermissionError, match="company data scope"):
        tool.handler(
            tool.validate({"sql": "SELECT customer FROM sales"}),
            None,
        )
    refusals = [e for e in audit.entries if e.outcome == "refused"]
    assert refusals and "company scope" in refusals[0].reason
    assert tool.handler(
        tool.validate({"sql": "SELECT product FROM sales"}),
        None,
    )


def test_answerer_scope_refusal_leaks_nothing(no_customer_store):
    result = answer_business_question("مين أكتر عميل اشترى؟", no_customer_store)
    assert result.refused
    assert result.rows == []
    assert "أحمد" not in result.render()
    month = answer_business_question("مبيعات الشهر ده كام؟", no_customer_store)
    assert month.known and not month.refused


def test_company_scope_composes_with_role_scope():
    store = seed_demo_store(
        company_scope=CompanyDataScope(allowed={"sales": FULL_SALES}),
    )
    cairo = BusinessScope(role="employee", branches=("القاهرة",))
    registry = registry_with_business_qa(store, scope=cairo)
    tool = registry.get("query_business_data")
    with pytest.raises(PermissionError, match="company data scope"):
        tool.handler(tool.validate({"sql": "SELECT * FROM inventory"}), None)
    rows = tool.handler(
        tool.validate({"sql": "SELECT branch FROM sales"}),
        None,
    )
    assert rows and all(row["branch"] == "القاهرة" for row in rows)
