# Build Brief: Slice 2 only

This is the Slice 2 portion of `BUILD_BRIEF.md`, extracted so it
can be handed over on its own. Slice 2 is the only slice ready to
execute now, because it needs neither D1 nor D2. The Blueprint
(`docs/ai-personal-agent-blueprint-v2.md`) is the specification
and wins on any conflict. Full acceptance criteria per issue are
in `docs/slice-2-issues.md`.

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

---

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

---

## 3. Blocked decisions and how to proceed anyway

Two decisions are open and gate the platform slices (see `docs/decisions/D1-D2-decision-brief.md`):

- **D1 identity provider** and **D2 hosted model provider and data region.**

Until they close, build against **interfaces and stubs**, not vendors:

- Define `AuthContext(user_id, workspace_id, roles)` and an `Authenticator` protocol. Use a fake authenticator in tests.
- Define a `ModelGateway` protocol (see §5, Slice 1) with a `FakeModelGateway` for tests. Do not hard-code any vendor.
- Do not send any private document to any provider.

Retention and deletion policy (D4) must close before any document upload or memory work. Embedding model choice (D5) must close before retrieval work.

---

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

---

## 8. Definition of done (every feature)

A user story; request and response schemas; authorization rules; unit and integration tests; defined failure behavior; logging without secrets; evaluation cases where behavior is model-dependent; documentation; a demo with realistic input; a disable switch for any external integration.

---

## 9. Questions to raise rather than guess

- Anything touching identity, tenant scope, approval binding, or retention: ask before choosing.
- Any requirement in this brief that conflicts with the Blueprint: stop and report; the Blueprint wins.
- Any place where a decision from §3 is needed to proceed: use the stub, note it in the PR, and do not pick a vendor.
