# AI Personal Assistant Agent

A general purpose AI personal assistant agent: it manages its owner's day and digital life, across calendar, mail, tasks, meetings, research, and files. It is designed technical first: architecture, tools, skills, and a structure to build on.

This repository is built step by step. **Step 1 is the Agent Orchestrator, the controller.**

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

Per tool timeout mechanism: each handler runs in a daemon thread and the orchestrator waits at most the timeout for it. Python cannot kill a thread, so a handler that ignores its timeout may finish in the background and dies with the process; the run never blocks on it. Side effecting handlers must be idempotent, and a process based executor is the documented upgrade path if hard kills are ever needed.

### Tool permissions it enforces

The model proposes. The policy decides. Every tool is registered with a permission level:

| Level | Meaning | Default behaviour |
| --- | --- | --- |
| `READ` | Reads only, no side effects | Runs automatically |
| `WRITE_REVERSIBLE` | A write that can be undone | Runs automatically, with a trace |
| `EXTERNAL_EFFECT` | Sends, books, or changes something outside | Needs human approval |
| `SENSITIVE` | Irreversible or high risk | Needs explicit approval every time |

If a tool needs approval and no approval callback is configured, the run stops cleanly with `waiting_for_approval` and the pending step attached. It never proceeds silently.

### Run statuses

Statuses follow the task state model: `queued`, `running`, `waiting_for_approval`, `completed`, `failed`, `cancelled`. Why a run stopped is reported separately in `stop_reason`: `goal_achieved`, `approval_required`, `permission_denied`, `timeout`, `step_limit_exceeded`, `validation_error`, `unknown_tool`, `retries_exhausted`, `goal_not_achieved`, `internal_error`. Failed runs carry a safe `error_summary` that never leaks internal exception details. Every run also carries a full trace: request, intent, plan, each tool choice and execution, retries, approvals, and the verification verdict.

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
tests/                 12 tests covering the cycle and every limit
examples/demo.py       End to end demo, including the permission gate
docs/architecture.md   How this step fits the full 8 layer design
```

## Design principles

1. The agent core is general. Specialisation lives in skills and tools added on top of it, never hard coded inside the core.
2. Tools are code, skills are descriptions. A tool does one real thing. A skill is a manifest that composes tools toward a goal.
3. The model proposes, policies decide. No external effect runs without the permission layer.
4. Every fact in an answer comes from a tool result or from memory, with a trace the user can review.
5. Memory is the product. What makes the assistant personal is what it remembers about its owner.

## Roadmap

- [x] **Step 1: Agent Orchestrator**, execution cycle, limits, and tool permissions
- [ ] Step 2: Tool families, calendar, mail, tasks, web, files
- [ ] Step 3: Skills layer, manifest engine and the first general skills
- [ ] Step 4: Memory, working, episodic, profile, and procedural
- [ ] Step 5: Proactivity, morning briefing, follow ups, evening review
- [ ] Later: LangGraph state machine core, MCP servers, voice channel

## License

MIT
