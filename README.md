# AI Personal Assistant Agent

A general purpose AI personal assistant agent: it manages its owner's day and digital life, across calendar, mail, tasks, meetings, research, and files. It is designed technical first: architecture, tools, skills, and a structure to build on.

This repository is built step by step. **Step 1 is the Agent Orchestrator, the controller.** Slice 2 hardened it into a resumable, tenant scoped, budget controlled core. The canonical design is the adopted blueprint in [docs/blueprint-v2.md](docs/blueprint-v2.md), and the canonical layer model lives in [docs/architecture.md](docs/architecture.md). One model everywhere: earlier drafts of this repo used a different 8 layer view, which is retired.

## Step 1: Agent Orchestrator, The Controller

The orchestrator manages the complete execution cycle:

```text
User Request
     |
Understand Intent
     |
Create Plan
     |
Choose Tool
     |
Execute Tool
     |
Observe Result
     |
Is the Goal Achieved?
     |-- No  -> Continue, Retry, or Ask the User
     |-- Yes -> Verify and Return Result
```

### Limits it enforces

No run is unbounded. Every run is controlled by `OrchestratorLimits`:

| Limit | Default | What happens when it is hit |
| --- | --- | --- |
| `max_steps` | 12 | Run stops with `step_limit_exceeded` |
| `max_execution_seconds` | 60 | Run stops with `timeout` |
| `max_retries_per_step` | 2 | Step retries, then the run asks the user |
| `max_replans` | 2 | Replanning stops, then the run asks the user |
| `per_tool_timeout_seconds` | 15 | The orchestrator stops waiting, records a timeout, and follows the retry path |
| `max_tool_calls` | 50 | Run stops as failed with `budget_exceeded` |
| `max_output_chars` | 20000 | Oversized tool output is truncated with a recorded marker |
| `max_tokens` | optional | Run stops as failed with `budget_exceeded` when usage reports exceed it |
| `max_cost` | optional | Same, for reported cost |

Per tool timeout mechanism: each handler runs in a daemon thread and the orchestrator waits at most the timeout for it. Python cannot kill a thread, so a handler that ignores its timeout may finish in the background and dies with the process; the run never blocks on it. Side effecting handlers must be idempotent, and a process based executor is the documented upgrade path if hard kills are ever needed.

### Tool permissions it enforces

The model proposes. The policy decides. Every tool is registered with a permission level:

| Level | Meaning | Default behaviour |
| --- | --- | --- |
| `READ` | Reads only, no side effects | Runs automatically |
| `WRITE_REVERSIBLE` | A write that can be undone | Runs automatically, with a trace |
| `EXTERNAL_EFFECT` | Sends, books, or changes something outside | Needs human approval |
| `SENSITIVE` | Irreversible or high risk | Needs explicit approval every time |

Approval is bound to the exact arguments: every request is recorded with a SHA-256 hash of the tool name and arguments. If a tool needs approval and no approval callback is configured, the run stops cleanly with `waiting_for_approval` and an approval record attached. `decide_approval` later resumes it from its checkpoint, and only if the arguments still hash to the approved value: changed arguments produce a fresh approval request instead of executing. Denial is recorded and ends the run with `permission_denied`.

Runs are also checkpointed after every observation (in memory, JSON file, or Postgres stores) and can be cancelled through a `CancellationToken` and resumed with `resume(task_id, context)` under the same tenant, with counters preserved so limits never reset. Traces, checkpoints, and audit events pass through a redactor, so secrets never reach storage.

### Run statuses

Statuses follow the task state model: `queued`, `running`, `waiting_for_approval`, `completed`, `failed`, `cancelled`. Why a run stopped is reported separately in `stop_reason`: `goal_achieved`, `approval_required`, `permission_denied`, `timeout`, `step_limit_exceeded`, `validation_error`, `unknown_tool`, `retries_exhausted`, `goal_not_achieved`, `internal_error`, `cancelled`, `budget_exceeded`. Failed runs carry a safe `error_summary` that never leaks internal exception details. Every run also carries a full trace: request, intent, plan, each tool choice and execution, retries, approvals, and the verification verdict.

## Quick start

```bash
pip install -e ".[dev]"
pytest -q
python examples/demo.py
```

```python
from personal_assistant_agent.orchestrator import (
    AgentOrchestrator, TenantContext, default_registry,
)

agent = AgentOrchestrator(registry=default_registry())
result = agent.run(
    "calculate 19 * 3 + 1",
    context=TenantContext(user_id="u1", workspace_id="ws1"),
)
print(result.status, result.answer)  # completed 58
```

## Structure

```text
src/personal_assistant_agent/
  orchestrator/
    orchestrator.py    The controller: the execution cycle loop
    limits.py          OrchestratorLimits: time, steps, retries, replans
    permissions.py     PermissionLevel and PermissionPolicy
    tools.py           Tool and ToolRegistry
    state.py           Intent, Plan, PlanStep, Observation, AgentState, AgentResult
    intent.py          Understand Intent (rule based starter, LLM replaceable)
    planner.py         Create Plan (rule based and static planners)
    verifier.py        Verify the goal before returning a result
    builtin_tools.py   Safe starter tools: echo, get_current_time, calculator
    approvals.py       Argument hash binding for approvals
    cancellation.py    CancellationToken checked across the whole run
    checkpoints.py     Checkpoint stores (memory, file, Postgres) and resume data
    audit.py           Redactor and audit events
    documents.py       search_documents over a tenant scoped corpus
  api.py               FastAPI app: task status endpoint
  platform/
    tenancy.py         Users, workspaces, memberships, invitations (our tables)
    identity.py        Identity provider boundary and dev provider
    gateway.py         Model gateway with per provider budget shares
    stub.py            Stub model adapter (the brief's fallback path)
    trial.py           The D2 trial: 30 tasks, runner, blind review pack
tests/                 75 tests covering the cycle, slice 2, and the platform
examples/demo.py       End to end demo, including the permission gate
docs/architecture.md   The canonical 10 layer model and invariants
docs/blueprint-v2.md   The adopted blueprint (version 2.0 revised)
```

## Design principles

1. The agent core is general. Specialisation lives in skills and tools added on top of it, never hard coded inside the core.
2. Tools are code, skills are descriptions. A tool does one real thing. A skill is a manifest that composes tools toward a goal.
3. The model proposes, policies decide. No external effect runs without the permission layer.
4. Every fact in an answer comes from a tool result or from memory, with a trace the user can review.
5. Memory is the product. What makes the assistant personal is what it remembers about its owner.

## Roadmap (blueprint section 18)

- [x] Phase 0: sources of truth, one canonical blueprint and layer model
- [x] Phase 1 (partial): backend foundation pieces in the orchestrator: tenant context, strict tool contracts
- [x] Phase 2: Slice 1 equivalent for the core: one safe read tool end to end (`search_documents`)
- [x] Phase 3 (partial): reliability and safety inside the orchestrator: timeouts, cancellation, checkpoints and resume, argument bound approvals, budgets, redaction, audit events, task status endpoint (Slice 2, issues 1 to 14, complete)
- [ ] Phase 4: memory and personalisation
- [ ] Phase 5: first complete user workflow with approvals
- [ ] Phase 6: artifacts
- [ ] Phase 7: proactivity
- [ ] Phase 8: messaging channels
- [ ] Phase 9: hardening and scale

Still open before wider building: decisions D1 to D6 in the blueprint need owners and dates. D1 (identity provider) and D2 (hosted provider and data region) gate the platform slices.

## License

MIT
