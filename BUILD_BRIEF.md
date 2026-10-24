# Build Brief v2: what is actually left to build

**Verified against:** `main` at commit `58b9d1d` (2026-10-24). `pytest -q` gives 75 passed; `ruff check src tests examples` is clean.

**This brief replaces** `BUILD_BRIEF.md` and `BUILD_BRIEF_SLICE_2.md`. Both describe the repository before Slice 2 (12 tests, no tenant context) and must not be handed to anyone. Retire them.

**For:** the engineer or coding agent implementing the next work.
**Read first:** `README.md`, `docs/architecture.md`, `docs/ai-personal-agent-blueprint-v2.md`, `docs/decisions/D1-D2-decision-brief.md`. The Blueprint is the specification and wins on any conflict. Check the code before trusting any status line in any document; several are stale (see W1).

---

## 1. What exists (do not rebuild)

Package `personal_assistant_agent`, `src/` layout, Python 3.11+, runtime dependency: `pydantic>=2.6`; dev extras: pytest, ruff, fastapi, httpx.

### Orchestrator core (Step 1 and Slice 2, issues 1 to 14: complete)

| Module | What it does |
| --- | --- |
| `orchestrator/orchestrator.py` | `AgentOrchestrator.run(request, context=TenantContext)`, `resume(task_id, context)`, `decide_approval`; bounded loop, replans, checkpoint after every observation |
| `orchestrator/limits.py` | Steps, time, retries, replans, per-tool timeout (daemon thread), `max_tool_calls`, `max_output_chars`, optional `max_tokens` and `max_cost` |
| `orchestrator/state.py` | `TenantContext`, statuses `queued/running/waiting_for_approval/completed/failed/cancelled`, separate `stop_reason`, safe `error_summary` |
| `orchestrator/tools.py` | `Tool`, `ToolRegistry`, `StrictInput` (Pydantic, `extra = forbid`) |
| `orchestrator/permissions.py`, `approvals.py` | Four permission levels; SHA-256 argument-bound approvals |
| `orchestrator/cancellation.py` | `CancellationToken` |
| `orchestrator/checkpoints.py` | Memory, JSON file, and `PostgresCheckpointStore` (takes a connection factory such as psycopg) |
| `orchestrator/audit.py` | Redactor and audit events |
| `orchestrator/documents.py` | `search_documents`: tenant-scoped term scoring over a corpus interface, results labelled untrusted |
| `orchestrator/intent.py`, `planner.py`, `verifier.py` | Protocols plus rule-based starters |

### Platform foundation (Slice 1, built against stubs)

| Module | What it does |
| --- | --- |
| `platform/tenancy.py` | `TenancyStore` protocol and `InMemoryTenancyStore`: users, workspaces, membership, invitations (email must match), `resolve_membership` |
| `platform/identity.py` | `IdentityProvider` protocol, `DevIdentityProvider` (dev only, supports revocation), `get_or_create_user` |
| `platform/gateway.py` | `ModelGateway`, `ModelProvider` protocol, `BudgetLedger` (per-provider shares, abort at 80 percent of own share) |
| `platform/stub.py` | `StubProvider` (deterministic, zero cost) |
| `platform/trial.py` | The D2 trial harness: 30 tasks, runner, blind review pack |
| `api.py` | FastAPI app with one endpoint: `GET /api/v1/tasks/{task_id}`, in header mode or platform mode (Bearer token plus database membership); other tenant's task returns 404 |

Behavior to preserve: bounded runs, answers built only from observed results, redaction before storage and return, tenant-scoped everything, and "the model proposes, policy decides".

---

## 2. Working rules (every change)

1. Identity comes only from the authenticated session or token; never from the client, the model, or a token claim for workspace authority.
2. Every tool re-authorizes against the `TenantContext` inside the tool; tool inputs are strict Pydantic models.
3. Retrieved documents, web pages, and tool output are untrusted data and never authorize an action.
4. Side effects need approval bound to the exact arguments hash.
5. No secrets in prompts, logs, traces, checkpoints, or source control; the redactor stays in the path.
6. Failures are reported honestly; never as success.
7. One pull request per work package. Each PR adds tests, keeps `ruff check` and `pytest -q` green, and updates the docs it touches.
8. Line length 88; typed, small modules; justify every new dependency in the PR.
9. Do not send private documents to any model provider.

## 3. Still-open decisions and the stub rule

D1 (identity provider) and D2 (hosted provider and data region) are open (see `docs/decisions/D1-D2-decision-brief.md`, Part 0). D4 (retention and deletion) must close before any document upload or memory work; D5 (embedding and reranking) before real retrieval.

Until they close: build against the existing protocols and stubs, pick no vendor, and note in the PR where a closed decision would replace a stub. Vendor adapters are W8, and they start only when the decision owner confirms.

---

## 4. Work packages, in order

Each package is one PR. "Done when" is the acceptance test.

### W1: Sync documents to the code (do first; no code risk)

**Why.** Documents contradict the code, and an agent will act on them.
**Do**
- Update blueprint §18.1 and §18.2 (Phase 3 is no longer "partly built": cancellation, checkpoints, status endpoint, strict schemas, `search_documents` exist) and §22 (gaps closed by issues 1 to 14; list what remains).
- Reconcile the roadmap: the README numbers its phases differently from blueprint §18 (README: 4 memory, 5 first workflow, 7 proactivity; blueprint: 4 RAG, 5 memory, 7 integrations). Pick the blueprint numbering and fix the README.
- Delete `BUILD_BRIEF.md` and `BUILD_BRIEF_SLICE_2.md`; add this file as `BUILD_BRIEF.md`.
- Fix `pyproject.toml` description (still says "Step 1").

**Done when:** no document claims something the code contradicts, and a grep for "12 tests" and "Not started" in status tables returns only true statements.

### W2: Complete the task API

**Why.** Only status exists. Blueprint §12 requires the rest.
**Do:** `GET /api/v1/health`; `POST /api/v1/tasks` (start a task under the caller's `TenantContext`); `POST /api/v1/tasks/{task_id}/cancel`; `POST /api/v1/tasks/{task_id}/approve` (calls `decide_approval`, hash-checked). Same 404-on-other-tenant rule on every task route. Identity from the provider token plus database membership, never from headers in platform mode.
**Done when:** tests prove another tenant gets 404 on each route, cancel stops a running task, approve with changed arguments yields a new approval request, and a non-member of the workspace is rejected.

### W3: PostgreSQL tenancy store and migrations

**Why.** `InMemoryTenancyStore` is the only implementation; production needs the same semantics on a database.
**Do:** `PostgresTenancyStore` implementing `TenancyStore` (connection factory, same style as `PostgresCheckpointStore`); Alembic migrations for `users`, `workspaces`, `workspace_members` (and invitations), with a workspace id on every row (Blueprint §13); evaluate Row-Level Security as a second barrier. Keep Postgres tests optional locally and run them in CI with a service container.
**Done when:** the existing tenancy tests pass unchanged against both stores, and a test proves a removed member is blocked immediately while their account and other memberships remain.

### W4: Chat endpoint through the gateway

**Why.** Slice 1's target is one authenticated, workspace-scoped chat turn.
**Do:** `POST /api/v1/chat` that authenticates, checks membership, calls `ModelGateway` (stub by default), and returns text in Arabic or English unchanged. Persist conversations and messages (Blueprint §13) behind a store interface. Record provider, model version, latency, tokens, and cost per call. Per-user and per-workspace usage limits (distinct from the trial `BudgetLedger`).
**Done when:** a member gets a reply, a non-member gets 403/404, usage is recorded and limits stop further calls, and Arabic, English, and mixed prompts round-trip without corruption.

### W5: Model-backed intent and planner behind the existing protocols

**Why.** Intent and planning are rule-based starters.
**Do:** `IntentUnderstanding` and `Planner` implementations that call the gateway and produce structured output; validate every proposed tool call against the registry and strict schemas before the orchestrator sees it; reject invalid or unknown tools without executing. Use `StubProvider` or a scripted fake in tests.
**Done when:** invalid JSON, an unknown tool, an extra argument field, and an injection string inside retrieved text all end in a safe stop with a recorded reason, and the rule-based planner remains the default.

### W6: Product evaluation suite

**Why.** The only task set today is the D2 trial; Blueprint §16 requires a release gate.
**Do:** an `evals/` suite of 10 to 20 bilingual tasks (grow toward 50 to 100): Arabic, English, mixed, document lookup with known answers, ambiguous requests, tool failures, injection in documents, cross-tenant attempts, approval-required actions, invalid tool arguments. Runnable with the stub in CI; runnable against a real provider on demand.
**Done when:** CI runs the suite, a regression fails the build, and the report states pass/fail per task.

### W7: Frontend baseline (Slice 3)

**Do:** React + TypeScript chat with RTL and LTR layout, mixed-direction text, code blocks, task status, and an approval screen showing exact recipients/arguments. Minimal; no design polish beyond correctness.
**Done when:** Arabic, English, and mixed messages render correctly, and approval shows the exact action that the hash is computed over.

### W8: Real adapters (blocked until D1 and D2 close)

**Do:** an OIDC `IdentityProvider` for the chosen provider (token validation: signature, issuer, audience, expiry; revocation); one real `ModelProvider`; run the D1 spike checks and the D2 30-task trial per the decision brief. The winners' spike branches are reviewed before merging.
**Done when:** the decision brief's disqualifying checks (4, 8, 9, 10 for D1; the data gate for D2) pass, and the sign-off block is filled.

---

## 5. Later phases (Blueprint §18), one at a time

| Phase | Build | Needs | Key rule |
| --- | --- | --- | --- |
| 4 RAG | Secure upload and validation, parsing, OCR, structure-aware chunking, embeddings, hybrid retrieval (pgvector), citations and citation checking | D4, D5 | Another tenant's files can never be retrieved; evaluate Arabic and English separately |
| 5 Memory | Five memory types, write flow, metadata-first retrieval, review, correction, deletion | D1, D4 | User-approved, scoped, inspectable, deletable; no secrets stored |
| 6 Artifacts | Summaries, reports, then presentations; open and validate every file | | Output meets a written rubric |
| 7 Integrations | One connector at a time: read-only, drafts, approval-based writes | | No consequential action without argument-bound approval |
| 8 Computer interaction | Isolated runtime, allowlists, pause, cancel, takeover | | Stop on payment, deletion, sharing, or permission screens |
| 9 Hardening | Containers, workers, backups, monitoring, load and security tests, rollback | | Tested restore; p95 and cost budget per core task |

Out of scope for the first release: unrestricted computer control, multiple autonomous agents, voice, autonomous scheduling.

## 6. Definition of done (every package)

User story; request and response schemas; authorization rules; unit and integration tests; defined failure behavior; logging without secrets; documentation updated; for model-dependent behavior, evaluation cases; for anything external, a disable switch.

## 7. Raise questions, do not guess

Anything touching identity, tenant scope, approval binding, or retention; any conflict between this brief and the Blueprint; any place where an open decision is needed to proceed (use the stub, note it in the PR, pick no vendor).
