# Slice 2 issues: orchestrator integration and hardening

Source: Blueprint v2, Appendix §22 (gap analysis). Each issue is one pull request. "Done when" is the acceptance test; an issue closes only when every item holds and the tests run in CI.

Suggested labels: `slice-2`, plus `security`, `reliability`, `api`, `docs` as marked.

Suggested order (by dependency):
`#1, #2, #3, #4` (independent) → `#5, #6, #10` → `#7, #8, #9` → `#11, #12` → `#13, #14` (anytime).

---

## #1 Preemptive per-tool timeout `reliability`

**Problem.** `per_tool_timeout_seconds` is checked after the handler returns, so a hung tool blocks the whole run past its limit.

**Done when**
- A tool exceeding the timeout is interrupted, recorded as a failed `Observation` with a timeout error, and handled by the normal retry path.
- A test uses a handler that never returns and the run still ends within `per_tool_timeout_seconds` plus a small margin.
- The total `max_execution_seconds` limit is still enforced and the existing time-limit test is tightened to assert `TIMEOUT` only.
- The chosen mechanism (thread, process, or async cancellation) and its limits are documented.

## #2 Strict Pydantic tool schemas `security`

**Problem.** `input_schema` is an informal dict and is never enforced.

**Done when**
- `Tool` takes a Pydantic input model; handlers receive a validated instance.
- Models use `extra = "forbid"`; unknown fields are rejected before execution and recorded as a validation error, never run.
- Built-in tools (`echo`, `get_current_time`, `calculator`) are migrated.
- Tests cover a missing field, a wrong type, and an unexpected extra field.

## #3 Tenant scope in state and tool authorization `security`

**Problem.** `AgentState` carries no `user_id` or `workspace_id`; tools cannot re-authorize (Blueprint §6.2 rule 4).

**Done when**
- `run()` requires an authenticated context (`user_id`, `workspace_id`) and stores it on `AgentState`.
- Tool handlers receive the context and must authorize against it; a tool called with another workspace's resource id fails.
- Cross-workspace test fails the build if scope is not enforced.
- Identity is never taken from the request text or tool arguments.

## #4 Align `RunStatus` with the task state model `api`

**Problem.** Enum lacks `queued`, `running`, `waiting_for_approval`, `cancelled`; `failed` is never produced.

**Done when**
- Statuses match Blueprint §7.2.
- `failed` is produced for non-recoverable errors (for example an unexpected internal exception), with a safe `error_summary`.
- README run-status section and tests are updated.

## #5 Task cancellation `reliability`

**Depends on:** #4.

**Done when**
- A running task can be cancelled via a cancellation token checked before each step, during retries, and inside long tool calls where possible.
- A cancelled run returns `cancelled` with completed steps and the pending step attached.
- Cancelling a finished task is a no-op with a clear response.
- Tests cover cancel before start, between steps, and during retry.

## #6 Checkpointing and resume `reliability`

**Depends on:** #4.

**Done when**
- State is persisted after every observation (store behind an interface; in-memory and PostgreSQL implementations).
- A run can resume from the last checkpoint without re-executing steps that already succeeded.
- Resume restores counters (`steps_executed`, `replans_used`) so limits are not reset.
- Test simulates a crash mid-plan and resumes to completion.

## #7 Argument-bound, resumable approval `security`

**Depends on:** #6.

**Problem.** Approval is an in-process callback with no argument binding and no pending state.

**Done when**
- A step needing approval ends the run as `waiting_for_approval` with an approval record: tool, target, arguments, and an arguments hash.
- Approval resumes the run only if the hash of the executed arguments matches the approved one; any change requires new approval.
- Denial yields `permission_denied` and is recorded.
- Tests include "approve, change arguments, execute" and expect a new approval request.

## #8 Token, cost, tool-call, and output-size budgets `reliability`

**Done when**
- `OrchestratorLimits` gains `max_tool_calls`, `max_output_chars`, and optional `max_tokens` and `max_cost`, with validation.
- Oversized tool output is truncated or rejected per policy and recorded.
- Hitting any budget stops the run with a distinct reason and the pending step.
- Tests exist for each new limit.

## #9 User-facing partial-result summary `api`

**Done when**
- Every non-completed result includes a plain-language summary: what completed, what did not, and why.
- Summary is built only from observations, never from model memory.
- Tests cover timeout, step limit, denied approval, and exhausted retries.

## #10 Trace redaction and persisted audit events `security`

**Done when**
- Trace and observations pass through a redactor (secrets, tokens, configured fields) before storage or return.
- Audit events are written for tool choice, approval, execution, and stop reason, with actor and workspace.
- Test proves a secret in tool arguments never appears in the stored trace.

## #11 Task status endpoint `api`

**Depends on:** #3, #4, #10.

**Done when**
- `GET /api/v1/tasks/{task_id}` returns status, current step, completed steps, and a safe error summary.
- A task from another user or workspace returns not found.
- Response never includes prompts, secrets, or raw tool traces.
- API integration tests cover own task, other user's task, and unknown id.

## #12 `search_documents` over a seeded corpus `slice-2`

**Depends on:** #2, #3.

**Done when**
- Tool uses a Pydantic input (`query` 2–500 chars, `top_k` 1–20) and returns typed results with `document_id`, `title`, `excerpt`, `source_location`.
- Search is filtered by workspace; a cross-workspace test returns nothing.
- Works on a small seeded Arabic and English corpus.
- Retrieved text is labelled as untrusted data in the planner or model context.

## #13 Reconcile architecture documents `docs`

**Done when**
- `README.md` and `docs/architecture.md` use one canonical layer model, matching Blueprint §4.
- Roadmap in the README matches Blueprint §18 phase names and statuses.

## #14 Retry-loop cleanup `docs`

**Done when**
- `Observation` is imported at module level in `orchestrator.py`.
- `_execute_step` has type annotations.
- Ruff passes with no new `noqa` entries.
