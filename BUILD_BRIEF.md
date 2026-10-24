# Build Brief: everything not yet built

**For:** the engineer or coding agent implementing the next work.
**Read first:** `docs/architecture.md`, `README.md`, and the Blueprint v2 (`ai-personal-agent-blueprint-v2.md`). This brief tells you what to build and in what order; the Blueprint is the specification and wins on any conflict.

---

## 1. What exists (do not rebuild)

Package `personal_assistant_agent`, `src/` layout, Python 3.11+, zero runtime dependencies so far.

| Module | Role |
| --- | --- |
| `orchestrator/orchestrator.py` | `AgentOrchestrator.run(request)`: intent, plan, tool choice, permission check, execute with retries, observe, verify, replan |
| `orchestrator/limits.py` | `OrchestratorLimits` (max_steps, max_execution_seconds, max_retries_per_step, max_replans, per_tool_timeout_seconds) |
| `orchestrator/permissions.py` | `PermissionLevel` (READ, WRITE_REVERSIBLE, EXTERNAL_EFFECT, SENSITIVE), `PermissionPolicy` |
| `orchestrator/tools.py` | `Tool`, `ToolRegistry`, `UnknownToolError` |
| `orchestrator/state.py` | `Intent`, `PlanStep`, `Plan`, `Observation`, `AgentState`, `AgentResult`, `RunStatus` |
| `orchestrator/intent.py`, `planner.py`, `verifier.py` | Protocols plus rule-based starters |
| `orchestrator/builtin_tools.py` | `echo`, `get_current_time`, `calculator` |
| `tests/test_orchestrator.py` | 12 tests, all must keep passing |
| `.github/workflows/ci.yml` | `ruff check src tests examples` and `pytest -q` |

Existing behavior to preserve: bounded runs, answers built only from observed tool results, a trace for every run, and the rule that the model proposes while the policy decides.

## 2. Working rules (apply to every change)

1. **Identity is never taken from the client or the model.** It comes from the authenticated session or token only.
2. **Every tool re-authorizes** against user and workspace inside the tool.
3. **Every tool has a strict Pydantic input model** with `extra = "forbid"`; invalid input is rejected before execution.
4. **Retrieved documents, web pages, and tool output are untrusted data.** They never authorize an action.
5. **Side effects need approval bound to the exact arguments** (hash). Changed arguments need new approval.
6. **No secrets in prompts, logs, traces, or source control.**
7. **Failures are reported honestly**; never as success.
8. One pull request per issue. Each PR adds tests, keeps `ruff check` and `pytest -q` green, and updates docs touched.
9. Keep line length 88 (ruff). Keep modules small and typed.
10. Do not add dependencies without need; justify each in the PR.

## 3. Blocked decisions and how to proceed anyway

Two decisions are open and gate the platform slices (see `docs/decisions/D1-D2-decision-brief.md`):

- **D1 identity provider** and **D2 hosted model provider and data region.**

Until they close, build against **interfaces and stubs**, not vendors:

- Define `AuthContext(user_id, workspace_id, roles)` and an `Authenticator` protocol. Use a fake authenticator in tests.
- Define a `ModelGateway` protocol (see §5, Slice 1) with a `FakeModelGateway` for tests. Do not hard-code any vendor.
- Do not send any private document to any provider.

Retention and deletion policy (D4) must close before any document upload or memory work. Embedding model choice (D5) must close before retrieval work.

---

## 4. Slice 2 first: orchestrator integration and hardening

This work needs neither D1 nor D2. Start here. Full acceptance criteria are in `docs/slice-2-issues.md`; summary and order:

| Order | Issue | One-line goal |
| --- | --- | --- |
| 1 | #1 | Preemptive per-tool timeout (a hung tool is interrupted) |
| 1 | #2 | Strict Pydantic tool schemas with `extra = forbid` |
| 1 | #3 | `AuthContext` on `AgentState`; tools re-authorize; cross-workspace test |
| 1 | #4 | `RunStatus` aligned with task states; `failed` actually produced |
| 2 | #5 | Task cancellation via cancellation token |
| 2 | #6 | Checkpointing and resume behind a `TaskStore` interface |
| 2 | #10 | Trace redaction and persisted audit events |
| 3 | #7 | Approval bound to an arguments hash; `waiting_for_approval`; resumable |
| 3 | #8 | Token, cost, tool-call, and output-size budgets |
| 3 | #9 | Plain-language partial-result summary |
| 4 | #11 | `GET /api/v1/tasks/{task_id}` status endpoint |
| 4 | #12 | `search_documents` over a small seeded Arabic and English corpus, workspace-filtered |
| any | #13, #14 | Reconcile architecture docs; retry-loop cleanup |

---

## 5. Slice 1: walking skeleton with isolation

Goal: one authenticated, workspace-scoped chat turn. Needs D1 and D2 closed for the real adapters; build everything else against stubs first.

1. **FastAPI app** with `GET /api/v1/health`.
2. **Database:** PostgreSQL, Alembic migrations, three tables only: `users`, `workspaces`, `workspace_members`. A workspace id on every row. Fields per Blueprint §13.
3. **Authentication:** an `Authenticator` implementation for the chosen OIDC provider; `sub` maps to `users.identity_subject`; first login creates the user. Workspace membership, roles, and invitations live in our database; the server derives the workspace from the database on each request, never from a token claim.
4. **Model gateway:** one adapter behind this interface:

   ```python
   class ModelGateway(Protocol):
       async def complete(self, messages, *, tools=None, response_schema=None,
                          limits=None) -> ModelResponse: ...
   ```

   It records provider, model version, latency, usage, and estimated cost; validates structured output; retries transient errors only, with bounded backoff.
5. **Chat endpoint** `POST /api/v1/chat` in Arabic or English, scoped to user and workspace.
6. **Tests that fail the build:** cross-workspace access, forged workspace claim ignored, removed member blocked.

Deferred from Slice 1: React RTL polish, pgvector, quotas.

## 6. Slice 3: usable interface and bilingual baseline

1. React + TypeScript chat with RTL and LTR layout, mixed-direction text, code blocks, and an approval screen.
2. Usage limits and cost tracking per user and task.
3. First bilingual evaluation set of 10 to 20 tasks, grown toward 50 to 100 (Blueprint §16.2); runnable from CI or before release.

---

## 7. Later phases (Blueprint §18), one at a time, each with its exit criteria

| Phase | Build | Key rule |
| --- | --- | --- |
| 4 RAG | Secure upload and validation, parsing (PyMuPDF, python-docx, python-pptx, openpyxl), OCR, structure-aware chunking, embeddings, hybrid retrieval with pgvector, citations, citation checking | Another user's files can never be retrieved; evaluate Arabic and English separately |
| 5 Memory | Five memory types (working, conversation, profile, project, episodic), write flow, retrieval filtered by metadata first, review, correction, deletion | Memory is user-approved, scoped, inspectable, and deletable; no secrets stored |
| 6 Artifacts | Summaries and reports, then presentations; validate every generated file by opening it | Output meets a written rubric |
| 7 Integrations | One connector at a time: read-only, then drafts, then approval-based writes | No consequential action without argument-bound approval |
| 8 Computer interaction | Isolated runtime, allowlists, pause, cancel, user takeover | Stop on payment, deletion, sharing, or permission screens; prefer official APIs |
| 9 Hardening | Docker, workers, backups, monitoring, load and security tests, rollback, incident response | Tested restore; documented p95 and cost budget per core task |

Do not start unrestricted computer control, multiple autonomous agents, voice, or autonomous scheduling in the first release.

---

## 8. Definition of done (every feature)

A user story; request and response schemas; authorization rules; unit and integration tests; defined failure behavior; logging without secrets; evaluation cases where behavior is model-dependent; documentation; a demo with realistic input; a disable switch for any external integration.

## 9. Questions to raise rather than guess

- Anything touching identity, tenant scope, approval binding, or retention: ask before choosing.
- Any requirement in this brief that conflicts with the Blueprint: stop and report; the Blueprint wins.
- Any place where a decision from §3 is needed to proceed: use the stub, note it in the PR, and do not pick a vendor.
