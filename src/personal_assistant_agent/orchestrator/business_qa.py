"""Business Q&A: read only questions over business data, answered
with evidence.

This is the first flagship application on top of the general agent
platform (see docs/business-qa.md). It adds one tool family and one
skill, and reuses everything else. Three rules are enforced here, in
code, not in prompts:

- The data tool is read only. Any write statement is rejected by
  validation, and the connection itself is opened query only after
  seeding, so a write cannot slip through a second path.
- Every answer has exactly three parts: the short answer, the query
  that was executed, and the source rows (or a summary when many).
- When a question cannot be matched confidently to a query, or the
  query returns nothing, the answer is an explicit "I don't know"
  fallback. A number is never invented.

Demo data only. Nothing here touches real business data, and no
credentials are involved.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from pydantic import Field

from .builtin_tools import default_registry
from .permissions import PermissionLevel
from .state import TenantContext
from .tools import StrictInput, Tool, ToolRegistry

UNKNOWN_ANSWER = (
    "مش عارف: السؤال ده مش متطابق مع أي استعلام متاح في النسخة "
    "التجريبية، ومفيش رقم هيتقال من غير دليل."
)

_WRITE_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "replace",
    "truncate",
    "vacuum",
    "attach",
    "detach",
    "pragma",
    "reindex",
)

_WRITE_PATTERN = re.compile(r"\b(" + "|".join(_WRITE_KEYWORDS) + r")\b", re.IGNORECASE)

_ARABIC_NORMALIZATION = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"}
)
_DIACRITICS = re.compile(r"[\u064b-\u0652]")


def normalize_arabic(text: str) -> str:
    """Normalize Egyptian Arabic text for keyword matching."""
    cleaned = _DIACRITICS.sub("", text)
    cleaned = cleaned.translate(_ARABIC_NORMALIZATION)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()


def validate_readonly_sql(sql: str) -> str:
    """Reject anything that is not a single read only SELECT.

    Raises ValueError for empty input, multi statement input, write
    keywords anywhere in the statement, and statements that do not
    start with SELECT or WITH. Comments are stripped before the
    first keyword check, so a write hidden behind a comment is
    still caught.
    """
    if not sql or not sql.strip():
        raise ValueError("empty query")
    stripped = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    stripped = re.sub(r"--[^\n]*", " ", stripped)
    stripped = stripped.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    if ";" in stripped:
        raise ValueError("only one statement is allowed")
    if _WRITE_PATTERN.search(stripped):
        raise ValueError("write statements are not allowed")
    first = stripped.split(None, 1)[0].lower() if stripped else ""
    if first not in ("select", "with"):
        raise ValueError("only SELECT queries are allowed")
    return stripped


@dataclass
class BusinessDataStore:
    """SQLite store for demo business data.

    Seeded in memory with small fixed demo tables, then switched to
    query only mode. The demo dates are relative to today, so the
    "this month" questions stay answerable whenever the demo runs.
    """

    connection: sqlite3.Connection = field(init=False)

    def __post_init__(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE sales (
                id INTEGER PRIMARY KEY,
                product TEXT NOT NULL,
                customer TEXT NOT NULL,
                branch TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                sale_date TEXT NOT NULL
            );
            CREATE TABLE inventory (
                id INTEGER PRIMARY KEY,
                product TEXT NOT NULL,
                branch TEXT NOT NULL,
                quantity_on_hand INTEGER NOT NULL,
                reorder_level INTEGER NOT NULL
            );
            """
        )
        self.connection = conn
        self._seed()
        conn.execute("PRAGMA query_only = ON")

    def _seed(self) -> None:
        today = date.today()
        this_month = today.replace(day=5).isoformat()
        last_month = (
            (today.replace(day=1) - timedelta(days=1)).replace(day=20).isoformat()
        )
        sales = [
            ("سماعات لاسلكية", "أحمد", "القاهرة", 2, 1500, this_month),
            ("شاحن سريع", "منى", "الجيزة", 5, 400, this_month),
            ("باور بانك", "خالد", "القاهرة", 3, 900, this_month),
            ("سماعات لاسلكية", "أحمد", "الإسكندرية", 1, 1500, this_month),
            ("جراب موبايل", "سارة", "القاهرة", 10, 150, this_month),
            ("شاحن سريع", "عمر", "الجيزة", 2, 400, last_month),
            ("باور بانك", "سارة", "القاهرة", 1, 900, last_month),
        ]
        self.connection.executemany(
            "INSERT INTO sales (product, customer, branch, quantity,"
            " unit_price, sale_date) VALUES (?, ?, ?, ?, ?, ?)",
            sales,
        )
        inventory = [
            ("سماعات لاسلكية", "القاهرة", 8, 10),
            ("شاحن سريع", "الجيزة", 50, 20),
            ("باور بانك", "القاهرة", 3, 5),
            ("جراب موبايل", "الإسكندرية", 100, 30),
        ]
        self.connection.executemany(
            "INSERT INTO inventory (product, branch, quantity_on_hand,"
            " reorder_level) VALUES (?, ?, ?, ?)",
            inventory,
        )
        self.connection.commit()

    def query(self, sql: str, limit: int = 50) -> list[dict]:
        """Run a validated read only query and return rows as dicts."""
        clean = validate_readonly_sql(sql)
        cursor = self.connection.execute(clean)
        rows = [dict(row) for row in cursor.fetchmany(limit)]
        return rows


def seed_demo_store() -> BusinessDataStore:
    return BusinessDataStore()


class BusinessQueryInput(StrictInput):
    sql: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=50, ge=1, le=100)


def make_query_business_data_tool(store: BusinessDataStore) -> Tool:
    def handler(args: BusinessQueryInput, context: TenantContext):
        # Demo data is shared demo data; there is no tenant data here.
        # Real tenant scoping lands with the real data connector.
        return store.query(args.sql, args.limit)

    return Tool(
        name="query_business_data",
        description=(
            "Run a read only SELECT query over the demo business data "
            "(sales and inventory tables). Writes are rejected."
        ),
        permission=PermissionLevel.READ,
        handler=handler,
        input_model=BusinessQueryInput,
    )


def registry_with_business_qa(store: BusinessDataStore) -> ToolRegistry:
    registry = default_registry()
    registry.register(make_query_business_data_tool(store))
    return registry


@dataclass
class AnswerWithEvidence:
    """An answer with exactly three parts: short answer, query, rows.

    When known is False, the answer is the explicit fallback, no
    query was run, and there are no rows to show.
    """

    answer: str
    query: str = ""
    rows: list[dict] = field(default_factory=list)
    known: bool = True

    def render(self, max_rows: int = 10) -> str:
        parts = [f"الإجابة: {self.answer}"]
        if self.known:
            parts.append(f"الاستعلام: {self.query}")
            if len(self.rows) > max_rows:
                shown = self.rows[:max_rows]
                parts.append(f"الدليل: {len(self.rows)} صف، أول {max_rows} منهم:")
                parts.extend(f"- {row}" for row in shown)
            else:
                parts.append(f"الدليل: {len(self.rows)} صف:")
                parts.extend(f"- {row}" for row in self.rows)
        else:
            parts.append("الاستعلام: لم يتم تنفيذ استعلام.")
            parts.append("الدليل: لا يوجد.")
        return "\n".join(parts)


_BRANCHES = ["القاهرة", "الجيزة", "الإسكندرية"]
_PRODUCTS = ["سماعات لاسلكية", "شاحن سريع", "باور بانك", "جراب موبايل"]


def _month_prefix() -> str:
    return date.today().isoformat()[:7]


def answer_business_question(
    question: str, store: BusinessDataStore
) -> AnswerWithEvidence:
    """Match an Egyptian Arabic question to a demo query.

    Deterministic keyword matching over the demo schema only. Any
    question outside the matched patterns returns the fallback,
    never a guessed number.
    """
    normalized = normalize_arabic(question)
    month = _month_prefix()

    if "مخزون" in normalized and any(
        word in normalized for word in ("هيخلص", "قرب يخلص", "ناقص", "قليل")
    ):
        sql = (
            "SELECT product, branch, quantity_on_hand, reorder_level"
            " FROM inventory WHERE quantity_on_hand <= reorder_level"
            " ORDER BY product"
        )
        rows = store.query(sql)
        if not rows:
            return AnswerWithEvidence(answer=UNKNOWN_ANSWER, known=False)
        names = "، ".join(row["product"] for row in rows)
        return AnswerWithEvidence(
            answer=f"المنتجات اللي مخزونها عند حد الطلب أو أقل: {names}.",
            query=sql,
            rows=rows,
        )

    if any(word in normalized for word in ("اكتر عميل", "اكبر عميل")):
        sql = (
            "SELECT customer, SUM(quantity * unit_price) AS total"
            " FROM sales GROUP BY customer ORDER BY total DESC LIMIT 1"
        )
        rows = store.query(sql)
        if not rows:
            return AnswerWithEvidence(answer=UNKNOWN_ANSWER, known=False)
        row = rows[0]
        return AnswerWithEvidence(
            answer=f"أكتر عميل اشترى هو {row['customer']} بإجمالي {row['total']:.0f} جنيه.",
            query=sql,
            rows=rows,
        )

    if "مبيعات" in normalized:
        for branch in _BRANCHES:
            if normalize_arabic(branch) in normalized:
                sql = (
                    "SELECT branch, SUM(quantity * unit_price) AS total"
                    f" FROM sales WHERE branch = '{branch}' GROUP BY branch"
                )
                rows = store.query(sql)
                if not rows:
                    return AnswerWithEvidence(answer=UNKNOWN_ANSWER, known=False)
                total = rows[0]["total"]
                return AnswerWithEvidence(
                    answer=f"إجمالي مبيعات فرع {branch}: {total:.0f} جنيه.",
                    query=sql,
                    rows=rows,
                )
        for product in _PRODUCTS:
            if normalize_arabic(product) in normalized:
                sql = (
                    "SELECT product, SUM(quantity * unit_price) AS total"
                    f" FROM sales WHERE product = '{product}' GROUP BY product"
                )
                rows = store.query(sql)
                if not rows:
                    return AnswerWithEvidence(answer=UNKNOWN_ANSWER, known=False)
                total = rows[0]["total"]
                return AnswerWithEvidence(
                    answer=f"إجمالي مبيعات {product}: {total:.0f} جنيه.",
                    query=sql,
                    rows=rows,
                )
        if any(word in normalized for word in ("الشهر ده", "الشهر الحالي")):
            sql = (
                "SELECT SUM(quantity * unit_price) AS total FROM sales"
                f" WHERE substr(sale_date, 1, 7) = '{month}'"
            )
            rows = store.query(sql)
            total = rows[0]["total"] if rows and rows[0]["total"] is not None else None
            if total is None:
                return AnswerWithEvidence(answer=UNKNOWN_ANSWER, known=False)
            return AnswerWithEvidence(
                answer=f"إجمالي مبيعات الشهر الحالي: {total:.0f} جنيه.",
                query=sql,
                rows=rows,
            )
        if any(word in normalized for word in ("كلها", "اجمالي", "الاجمالي")):
            sql = "SELECT SUM(quantity * unit_price) AS total FROM sales"
            rows = store.query(sql)
            total = rows[0]["total"] if rows and rows[0]["total"] is not None else None
            if total is None:
                return AnswerWithEvidence(answer=UNKNOWN_ANSWER, known=False)
            return AnswerWithEvidence(
                answer=f"إجمالي المبيعات كلها: {total:.0f} جنيه.",
                query=sql,
                rows=rows,
            )

    return AnswerWithEvidence(answer=UNKNOWN_ANSWER, known=False)
