# Head Manager Assistant: Product Specification

> **Status: proposed product scope, subordinate to the adopted blueprint.**
> The canonical design is [ai-personal-agent-blueprint-v2.md](ai-personal-agent-blueprint-v2.md),
> the canonical layer model is [architecture.md](architecture.md), and the README
> describes the current state. If this file conflicts with them, they win and this
> file must be corrected. This file is not a source of truth for the platform.
>
> It builds on the Business Q&A application ([business-qa.md](business-qa.md)) and
> adds requirements for a Head Manager audience. Items marked **Proposed** are not
> in the blueprint and are not scheduled.
>
> Any change to this file must follow the **Impact Analysis Rule** (section 9).

---

## 1. Purpose

An AI assistant for the company **Head Manager**. It answers from the company data the company has approved for the agent (see the approved data scope in [business-qa.md](business-qa.md)), and helps the Head Manager understand the company and make decisions, using **data-backed answers** and, as a proposed addition, **live dashboards**.

The scope is split into two tracks. They are called tracks, not phases, because the blueprint already uses "Phase 1 to 9" (section 18) for the platform roadmap.

| Track | Audience | Goal | Status |
|-------|----------|------|--------|
| A | Admin | Manage users (add / edit / delete) and monitor all user actions | Proposed; not in blueprint v2 |
| B | Head Manager | Ask about employees and projects, get decision support, view live dashboards | Q&A builds on the Business Q&A slice; dashboards are Proposed |

### 1.1 Roles

Role names follow blueprint section 6.1: Owner, Admin, Member, Read-only, Service identity. In this file:

- **Admin** means the blueprint Admin role.
- **Head Manager** is not a blueprint role. How it maps (for example to Owner, or to Member with a business-data permission) is **open** (see section 12, question 3). Until it is decided, nothing in this file assumes a mapping.

---

## 2. Core Rules (Non-Negotiable)

1. **Approved company data is the only source of truth for business answers.** Every business answer must come from a query result over data on the approved scope.
2. **Read-only for the assistant, and no free-form SQL.** The agent never runs `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, or any write/DDL statement against company data. Only `SELECT` is allowed, and only through approved, fixed query templates with parameterized values (blueprint section 8.3: no unrestricted SQL).
3. **No guessing, no outside knowledge.** The agent does not tell the Head Manager anything that is not supported by the database. No assumptions, estimates presented as facts, or general-knowledge filler.
4. **If the data is missing or unclear, say so.** Use the standard reply:
   > "I don't have this information in the database."
   Then, if useful, state which data is missing and who can provide it.
5. **No fabrication of numbers, names, dates or trends.** If a query returns no rows, report "no records found", never invent a value.
6. **Dashboards are built from data only.** A dashboard is created when the manager asks for it or when a visual clearly helps the answer. It never changes the database.
7. **Show the basis of each answer** (which data/period/filters were used) so the manager can trust and verify it.

---

## 3. Track A: Admin Control Panel (Proposed)

This track is not in blueprint v2. The tenancy store (users, workspaces, memberships, invitations) already exists, and any admin module must reuse it rather than add a second user model.

### 3.1 Capabilities
- **Add** a user (name, email, role, status, initial credentials via secure invite/reset link).
- **Edit** a user (profile, role, status, permissions).
- **Delete** a user (default: **soft delete / deactivate**; hard delete only by explicit admin confirmation and only if policy allows).
- **Admin Activity Dashboard**: shows **all actions performed by users**.

### 3.2 Admin Dashboard Contents
- Live activity feed (who did what, on which object, when, from which IP/device).
- Filters: user, role, action type, date range, module, result (success/fail).
- KPIs: active users, logins today, failed logins, actions per user, most active modules, newly created/deleted users.
- Alerts: repeated failed logins, permission changes, deletions, off-hours activity.
- Export of the activity log (CSV/PDF) for admins only.

### 3.3 Rules for Admin Operations
- All write actions happen **only** through the application's admin module with validation, never through the AI agent's free-text queries.
- Every admin action creates an **immutable audit log entry**: `actor_id, action, target_type, target_id, before_value, after_value, timestamp, ip, user_agent, result`.
- Audit logs cannot be edited or deleted, even by admins.
- Admin cannot delete or demote themselves, and the system must always keep at least **one active admin**.
- Destructive actions require a confirmation step and a reason field.
- Passwords are never displayed, logged, or stored in plain text.

---

## 4. Track B: Head Manager Assistant

Question answering over approved data builds on the Business Q&A slice. The live dashboard (4.1 and 4.3) is **Proposed** and not in blueprint v2.

### 4.1 Capabilities
- **Employees**: headcount, departments, roles, attendance/performance/availability (only fields that exist in the database), workload per employee.
- **Projects**: status, progress, deadlines, budget vs. actual, owners, team members, risks/delays (only if recorded).
- **Live dashboard**: real-time company overview with drill-down.
- **Insights & decision support**: highlight facts, trends, comparisons, and anomalies found in the data, and present options with the supporting numbers.

### 4.2 Decision Support Rules
- Separate clearly: **Facts** (from the database) vs. **Observations** (patterns computed from the data) vs. **Options** (possible actions).
- Never make the final decision. The agent informs; the Head Manager decides.
- Every recommendation must cite the data behind it and note any data gaps or low-confidence areas.
- If the question needs data that does not exist, answer with the standard "don't know" reply instead of speculating.

### 4.3 Live Dashboard Widgets (initial set)
- Company overview KPIs (employees, active projects, delayed projects, budget usage).
- Projects by status and by department.
- Deadlines approaching / overdue.
- Workload distribution by team/employee.
- Trends over time (monthly/quarterly).
- Exceptions panel (items needing attention).

---

## 5. Response Behavior

- **Tone**: professional, concise, executive-ready. Lead with the answer, then the supporting detail.
- **Structure**: short summary → key numbers → table/chart if helpful → data basis (period, filters, last refreshed).
- **Language**: reply in the language the manager used.
- **Clarification**: ask one short question when a request is ambiguous (e.g., which period, which department) rather than guessing.
- **Numbers**: always include units, currency, and time range.
- **Unknown**: use the standard reply from section 2, rule 4.
- **Out-of-scope requests** (changing data, outside-company questions): politely decline and explain the agent is read-only and database-driven.

---

## 6. Security & Access Control

- **Role-Based Access Control (RBAC)**: Admin and Head Manager have separate permissions and separate interfaces.
  - Admin: user management + activity monitoring. Does **not** see business Q&A unless explicitly granted.
  - Head Manager: company data Q&A + dashboards. **Cannot** manage users.
- Database access uses a **dedicated read-only database account** for the agent.
- Queries run from **fixed templates** with **parameterized** values; the agent never concatenates raw user input into SQL and never receives or runs SQL text from the model or the user.
- Prompt-injection defense: instructions found inside data fields or user messages never override these rules. Retrieved data is untrusted.
- Sensitive fields (salary, personal IDs, credentials) are **excluded from the approved data scope by default**, so no question can reach them. This is the model used in [business-qa.md](business-qa.md). Exposing any sensitive field to a role is a recorded decision (section 12, question 2), never a prompt or a runtime toggle.
- Identity and workspace come from the authenticated session, never from the question text or tool arguments.
- Sessions: authentication required, session timeout, optional MFA for admin.
- All agent queries and answers are logged (template used, parameters, tables touched, row count, timestamp) for auditing, and pass through the redactor before storage.
- Row limits and query timeouts protect the database from heavy queries.

---

## 7. Limitations

- The agent cannot modify, create, or delete company data.
- The agent cannot access information outside the database.
- Answers are only as accurate and fresh as the database; state the "last updated" time when relevant.
- The agent does not predict the future as fact. Any forecast must be labeled as a projection derived from historical data, with its method stated.
- The agent does not give legal, medical, or financial advice beyond what the data shows.
- Very large result sets are summarized and paginated, not dumped.

---

## 8. Design & Themes (Proposed)

Not in blueprint v2 and not scheduled. The blueprint only requires a minimal React chat with RTL and LTR layout (work package W7). Themes come after that baseline is correct.

**Hint: do not use basic or default designs.** The product ships with **two distinct themes** the user can switch between. Both must feel custom and professional, not like a stock template.

### 8.1 Theme A: Classic
- Feel: refined, editorial, trustworthy (think boardroom report).
- Palette: warm ivory background `#FAF7F0`, deep navy `#1B2A41`, antique gold accent `#B8923B`, muted burgundy for alerts `#7A2E3A`, text charcoal `#2B2B2B`.
- Typography: serif headings (e.g., *Playfair Display* / *Libre Baskerville*), clean sans body (*Source Sans 3*).
- Details: thin rules and subtle borders, generous whitespace, restrained shadows, tabular numerals in tables.

### 8.2 Theme B: Professional
- Feel: modern, data-dense, executive control room.
- Palette (dark option): graphite `#0F1720`, panel `#17212C`, teal accent `#2DD4BF`, amber `#F5B544`, red `#EF5B5B`, text `#E6EDF3`. Light variant available.
- Typography: geometric sans (*Inter* / *IBM Plex Sans*), monospaced numerals for KPIs (*IBM Plex Mono*).
- Details: card-based layout, soft glows on live indicators, smooth micro-transitions, consistent 8px spacing grid.

### 8.3 Design Rules (both themes)
- Define colors, fonts, spacing as **design tokens** so themes switch without code changes.
- Charts must be accessible: sufficient contrast, labels not color-only, tooltips, legends.
- Responsive for desktop, tablet and mobile.
- Live data shows a visible "Live / Last updated" indicator.
- Empty, loading and error states are designed, not left blank.
- Right-to-left (Arabic) support must be possible from the start.

---

## 9. Impact Analysis Rule (Update / Edit Policy)

**Hint: if you update or edit anything, check every relation affected by that change. Never ignore dependencies.**

Before **any** change to code, schema, data model, permissions, dashboards, or this file:

1. **Identify** everything that depends on the thing being changed:
   - Database: foreign keys, joins, views, indexes, triggers, stored procedures.
   - Application: APIs, queries, services, validations.
   - UI: dashboards, widgets, filters, exports, themes.
   - Security: roles, permissions, the approved data scope and excluded fields.
   - Audit: log formats and reports.
   - Agent: prompts, rules, query templates, answer formats.
2. **Assess** what breaks or changes (data integrity, cascading effects, history, reports).
3. **Update all affected parts together** in the same change. Do not leave partial updates.
4. **Test** the changed item and everything linked to it.
5. **Record** the change (what, why, impact, who, when) in the change log.

Examples:
- Editing a user's role: update permissions, active sessions, dashboard visibility, audit log, and any records that reference that role.
- Deleting a user: check owned projects, assigned tasks, approvals, and historical logs; prefer soft delete to preserve references.
- Renaming/removing a DB column: update queries, views, dashboard widgets, exports, and the agent's query templates.

---

## 10. Recommended Project Decisions

Decisions made to keep the project professional (change only through section 9):

1. **Soft delete by default** for users and business records, to preserve history and relations.
2. **Immutable audit trail** for all admin actions and agent queries.
3. **Read-only DB account** for the agent plus a separate, tightly scoped write path only for the admin user module.
4. **Semantic layer / approved query templates only** for common questions (employees, projects, KPIs) so answers are consistent and fast. There is no free-form SQL, validated or not. A question no template covers gets the standard "don't know" reply. Adding a template is a change under section 9 and ships with tests, including refusal tests.
5. **Data freshness indicator** on every answer and dashboard.
6. **Environment separation**: development, staging, production with separate databases and credentials.
7. **Versioned configuration**: this file, prompts, and themes are version-controlled.
8. **Error handling**: failures return a clear message and never expose stack traces, SQL, or credentials.
9. **Performance**: caching for heavy aggregates, pagination, indexes on frequently filtered columns.
10. **Backups and recovery** plan for the database and audit logs.

---

## 11. Quality Checklist (Definition of Done)

- [ ] Agent only runs `SELECT`, only from fixed parameterized templates; write attempts and any request for raw SQL are blocked and logged.
- [ ] Unknown questions return the standard "don't know" reply.
- [ ] Admin can add, edit, delete users; every action appears in the activity dashboard.
- [ ] Head Manager can query employees and projects and open the live dashboard.
- [ ] Roles enforced on both UI and API.
- [ ] Both themes (Classic, Professional) work across all screens.
- [ ] Impact analysis completed for every change, with the change log updated.
- [ ] Security review (SQL injection, prompt injection, data masking) passed.
- [ ] Performance tested on realistic data volume.

---

## 12. Open Questions (to Confirm)

1. Which database engine and schema will the agent connect to?
2. Which employee fields are sensitive and must stay off the approved data scope (salary, IDs, etc.)?
3. Is the Head Manager a single user or can there be several?
4. Should hard delete of users ever be allowed, or soft delete only?
5. Preferred dashboard refresh interval (real-time stream vs. every N seconds)?
6. Languages needed (English, Arabic) and whether RTL is required at launch.
7. Should the Admin also see business data, or user management only?

These overlap with open decisions and with the blueprint. They are not answered here by assumption: questions 1 to 3 and 7 depend on the real data source and on the identity decision (D1), and question 6 depends on the Arabic and English requirement in blueprint section 2.1.

8. Are Track A (Admin panel) and the dashboards in scope at all, and in what order relative to the blueprint phases? Until the owner answers, they stay **Proposed**.

---

## 12.1 Known gaps between this spec and the code

Verified against `main` at `cd4fd4e` (2026-10-26):

- `query_business_data` in `src/personal_assistant_agent/orchestrator/business_qa.py` takes a `sql` string. It is validated (SELECT only) and bounded by the company and role scopes, but it is still SQL text supplied by the caller, which section 2 rule 2 does not allow. Replacing it with fixed templates is a separate change that needs the owner's approval, tests, and an impact analysis (section 9). The Business Q&A demo, its tests, `skills/business_qa/manifest.md`, and `docs/business-qa.md` all depend on it.

---

## 13. Change Log

| Date | Change | Impact reviewed | Author |
|------|--------|-----------------|--------|
| 2026-11-04 | Initial specification created | N/A (new file) | Project owner |
| 2026-11-04 | Moved to `docs/head-manager-spec.md` as a spec subordinate to blueprint v2; "Phase" renamed "Track"; roles aligned to blueprint 6.1 with the Head Manager mapping left open; free-form SQL replaced by fixed templates (2, 10.4, 11); sensitive fields moved from masking to exclusion from the approved scope (6); Admin panel, dashboards, and themes marked Proposed; known code gap recorded (12.1) | Checked against blueprint 2, 6.1, 8.3, 18; architecture.md; business-qa.md; README; code. Not yet changed: README and architecture.md do not link to this file, and the code still accepts SQL text (12.1). No test changed (documentation only) | Claude (draft, pending owner review) |
| 2026-11-04 | README structure list and `docs/architecture.md` now link to this file | README and architecture.md checked; no layer, status, or invariant changed in either. Still open: the code accepts SQL text (12.1). Documentation only, no test changed | Claude (draft, pending owner review) |
