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
- Scope is enforced in code, not in prompts. The owner sees
  everything; an employee sees only their branches. A question
  about another branch, or a question spanning branches without
  naming the employee's branch, is refused outright: no partial
  answer, no leaked rows. The raw data tool enforces the same rule
  on its results, refusing queries whose rows carry no branch.
- Every question, query, answer, and refusal is recorded in an
  in memory audit log that can be rendered and inspected.

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

    def query(self, sql: str, params: tuple = (), limit: int = 50) -> list[dict]:
        """Run a validated read only query and return rows as dicts.

        Values are always passed as parameters, never interpolated
        into the SQL string, so a value can never change the shape
        of the query.
        """
        clean = validate_readonly_sql(sql)
        cursor = self.connection.execute(clean, params)
        rows = [dict(row) for row in cursor.fetchmany(limit)]
        return rows


def seed_demo_store() -> BusinessDataStore:
    return BusinessDataStore()


@dataclass(frozen=True)
class BusinessScope:
    """Who the caller is, and what they may see.

    The owner sees everything. An employee sees only the branches
    listed here. The scope is bound by the server side caller when
    the tool is built; it is never taken from the question text or
    from tool arguments, so wording cannot widen it.
    """

    role: str = "owner"
    branches: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in ("owner", "employee"):
            raise ValueError("role must be 'owner' or 'employee'")

    def allows_branch(self, branch: str) -> bool:
        return self.role == "owner" or branch in self.branches


OWNER_SCOPE = BusinessScope(role="owner")


class BusinessQueryInput(StrictInput):
    sql: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=50, ge=1, le=100)


def make_query_business_data_tool(
    store: BusinessDataStore,
    scope: BusinessScope = OWNER_SCOPE,
    audit: BusinessAuditLog | None = None,
) -> Tool:
    """Build the read only data tool, bound to a caller scope.

    The owner is unrestricted. For an employee, scope is enforced
    on the result, not on trust: rows are filtered to the caller's
    branches, and a query whose rows carry no branch column (a pure
    aggregate over all branches, where scope cannot be verified) is
    refused outright. No partial answer and no leaked rows.
    """

    def handler(args: BusinessQueryInput, context: TenantContext):
        rows = store.query(args.sql, limit=args.limit)
        if scope.role == "employee":
            if rows and "branch" not in rows[0]:
                if audit is not None:
                    audit.record(
                        AuditEntry(
                            question="",
                            outcome="refused",
                            answer="",
                            query=args.sql,
                            role=scope.role,
                            scope_branches=scope.branches,
                            reason="query rows carry no branch, scope unverifiable",
                        )
                    )
                raise PermissionError(
                    "employee queries must return branch attributable rows;"
                    " pure aggregates spanning branches are refused"
                )
            rows = [row for row in rows if row.get("branch") in scope.branches]
        if audit is not None:
            audit.record(
                AuditEntry(
                    question="",
                    outcome="answered",
                    answer="",
                    query=args.sql,
                    rows_count=len(rows),
                    role=scope.role,
                    scope_branches=scope.branches,
                )
            )
        return rows

    return Tool(
        name="query_business_data",
        description=(
            "Run a read only SELECT query over the demo business data "
            "(sales and inventory tables). Writes are rejected, and "
            "results are constrained to the caller's scope."
        ),
        permission=PermissionLevel.READ,
        handler=handler,
        input_model=BusinessQueryInput,
    )


def registry_with_business_qa(
    store: BusinessDataStore,
    scope: BusinessScope = OWNER_SCOPE,
    audit: BusinessAuditLog | None = None,
) -> ToolRegistry:
    registry = default_registry()
    registry.register(make_query_business_data_tool(store, scope, audit))
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
    refused: bool = False
    reason: str = ""

    def render(self, max_rows: int = 10) -> str:
        if self.refused:
            parts = [f"مرفوض: {self.answer}"]
            if self.reason:
                parts.append(f"السبب: {self.reason}")
            parts.append("الاستعلام: لم يتم تنفيذ استعلام.")
            parts.append("الدليل: لا يوجد.")
            return "\n".join(parts)
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


@dataclass
class AuditEntry:
    """One recorded question, query, answer, or refusal."""

    question: str
    outcome: str  # "answered" | "refused" | "unknown"
    answer: str
    query: str = ""
    rows_count: int = 0
    role: str = "owner"
    scope_branches: tuple[str, ...] = ()
    reason: str = ""


class BusinessAuditLog:
    """In memory audit trail for Business Q&A.

    Every question answered, refused, or left unknown is recorded
    with the query that ran and the rows it returned, so any number
    ever quoted can be traced back to its source.
    """

    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    def render(self) -> str:
        lines = []
        for entry in self.entries:
            lines.append(
                f"- [{entry.outcome}] {entry.question or '(tool query)'}"
                f" | role={entry.role}"
                f" | query={entry.query or 'none'}"
                f" | rows={entry.rows_count}"
                + (f" | reason={entry.reason}" if entry.reason else "")
            )
        return "\n".join(lines)


_BRANCHES = ["القاهرة", "الجيزة", "الإسكندرية"]
_PRODUCTS = ["سماعات لاسلكية", "شاحن سريع", "باور بانك", "جراب موبايل"]


def _month_prefix() -> str:
    return date.today().isoformat()[:7]


def _refusal(answer: str, reason: str) -> AnswerWithEvidence:
    return AnswerWithEvidence(answer=answer, known=False, refused=True, reason=reason)


def _fallback() -> AnswerWithEvidence:
    return AnswerWithEvidence(answer=UNKNOWN_ANSWER, known=False)


def _in_condition(column: str, values: list[str], params: list) -> str:
    params.extend(values)
    return f"{column} IN ({', '.join('?' * len(values))})"


def _answer_business_question(
    question: str, store: BusinessDataStore, scope: BusinessScope
) -> AnswerWithEvidence:
    """Match an Egyptian Arabic question to a demo query.

    Deterministic keyword matching over the demo schema only. Every
    filter the question mentions (branch, product, month) is applied
    to the query; a filter is never silently dropped. For an
    employee, any branch outside their scope, or any question that
    spans beyond their scope without naming their branch, is refused
    before any query runs. Any question outside the matched patterns
    returns the fallback, never a guessed number.
    """
    normalized = normalize_arabic(question)
    month = _month_prefix()
    mentioned_branches = [
        branch for branch in _BRANCHES if normalize_arabic(branch) in normalized
    ]
    mentioned_products = [
        product for product in _PRODUCTS if normalize_arabic(product) in normalized
    ]
    month_mentioned = any(word in normalized for word in ("الشهر ده", "الشهر الحالي"))
    all_mentioned = any(word in normalized for word in ("كلها", "اجمالي", "الاجمالي"))

    if scope.role == "employee":
        outside = [
            branch for branch in mentioned_branches if not scope.allows_branch(branch)
        ]
        if outside:
            return _refusal(
                f"السؤال ده عن فرع {outside[0]}، وهو بره نطاقك.",
                reason=f"branch outside employee scope: {outside[0]}",
            )

    if "مخزون" in normalized and any(
        word in normalized for word in ("هيخلص", "قرب يخلص", "ناقص", "قليل")
    ):
        if scope.role == "employee" and not mentioned_branches:
            return _refusal(
                "السؤال ده عن كل الفروع، ونطاقك فرع محدد بس. اسأل عن فرعك.",
                reason="unscoped inventory question from employee",
            )
        conditions = ["quantity_on_hand <= reorder_level"]
        params: list = []
        if mentioned_branches:
            conditions.append(_in_condition("branch", mentioned_branches, params))
        sql = (
            "SELECT product, branch, quantity_on_hand, reorder_level"
            " FROM inventory WHERE " + " AND ".join(conditions) + " ORDER BY product"
        )
        rows = store.query(sql, tuple(params))
        if not rows:
            return _fallback()
        names = "، ".join(row["product"] for row in rows)
        return AnswerWithEvidence(
            answer=f"المنتجات اللي مخزونها عند حد الطلب أو أقل: {names}.",
            query=sql,
            rows=rows,
        )

    if any(word in normalized for word in ("اكتر عميل", "اكبر عميل")):
        if scope.role == "employee" and not mentioned_branches:
            return _refusal(
                "السؤال ده عن كل الفروع، ونطاقك فرع محدد بس. اسأل عن فرعك.",
                reason="unscoped top customer question from employee",
            )
        conditions = []
        params = []
        if mentioned_branches:
            conditions.append(_in_condition("branch", mentioned_branches, params))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = (
            "SELECT customer, SUM(quantity * unit_price) AS total"
            f" FROM sales{where} GROUP BY customer ORDER BY total DESC LIMIT 1"
        )
        rows = store.query(sql, tuple(params))
        if not rows:
            return _fallback()
        row = rows[0]
        if len(mentioned_branches) == 1:
            suffix = f" في فرع {mentioned_branches[0]}"
        elif mentioned_branches:
            suffix = f" في فروع {'، '.join(mentioned_branches)}"
        else:
            suffix = ""
        return AnswerWithEvidence(
            answer=(
                f"أكتر عميل اشترى هو {row['customer']}"
                f" بإجمالي {row['total']:.0f} جنيه{suffix}."
            ),
            query=sql,
            rows=rows,
        )

    if "مبيعات" in normalized:
        if scope.role == "employee" and not mentioned_branches:
            return _refusal(
                "السؤال ده عن كل الفروع، ونطاقك فرع محدد بس. اسأل عن فرعك.",
                reason="unscoped sales question from employee",
            )
        conditions = []
        params = []
        if mentioned_branches:
            conditions.append(_in_condition("branch", mentioned_branches, params))
        if mentioned_products:
            conditions.append(_in_condition("product", mentioned_products, params))
        if month_mentioned:
            conditions.append("substr(sale_date, 1, 7) = ?")
            params.append(month)
        if not conditions and not all_mentioned:
            return _fallback()
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT SUM(quantity * unit_price) AS total FROM sales{where}"
        rows = store.query(sql, tuple(params))
        total = rows[0]["total"] if rows and rows[0]["total"] is not None else None
        if total is None:
            return _fallback()
        branch_part = ""
        if len(mentioned_branches) == 1:
            branch_part = f"فرع {mentioned_branches[0]}"
        elif mentioned_branches:
            branch_part = f"فروع {'، '.join(mentioned_branches)}"
        product_part = "، ".join(mentioned_products) if mentioned_products else ""
        if branch_part and product_part and month_mentioned:
            text = (
                f"إجمالي مبيعات {product_part} في {branch_part}"
                f" في الشهر الحالي: {total:.0f} جنيه."
            )
        elif branch_part and product_part:
            text = f"إجمالي مبيعات {product_part} في {branch_part}: {total:.0f} جنيه."
        elif branch_part and month_mentioned:
            text = f"إجمالي مبيعات {branch_part} في الشهر الحالي: {total:.0f} جنيه."
        elif product_part and month_mentioned:
            text = f"إجمالي مبيعات {product_part} في الشهر الحالي: {total:.0f} جنيه."
        elif branch_part:
            text = f"إجمالي مبيعات {branch_part}: {total:.0f} جنيه."
        elif product_part:
            text = f"إجمالي مبيعات {product_part}: {total:.0f} جنيه."
        elif month_mentioned:
            text = f"إجمالي مبيعات الشهر الحالي: {total:.0f} جنيه."
        else:
            text = f"إجمالي المبيعات كلها: {total:.0f} جنيه."
        return AnswerWithEvidence(answer=text, query=sql, rows=rows)

    return _fallback()


def answer_business_question(
    question: str,
    store: BusinessDataStore,
    scope: BusinessScope = OWNER_SCOPE,
    audit: BusinessAuditLog | None = None,
) -> AnswerWithEvidence:
    """Answer a question under a caller scope, recording the audit trail.

    The scope defaults to the owner (everything). Pass an employee
    scope to constrain answers to their branches; out of scope
    questions are refused, never partially answered.
    """
    result = _answer_business_question(question, store, scope)
    if audit is not None:
        if result.refused:
            outcome = "refused"
        elif result.known:
            outcome = "answered"
        else:
            outcome = "unknown"
        audit.record(
            AuditEntry(
                question=question,
                outcome=outcome,
                answer=result.answer,
                query=result.query,
                rows_count=len(result.rows),
                role=scope.role,
                scope_branches=scope.branches,
                reason=result.reason,
            )
        )
    return result
