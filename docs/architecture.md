# Architecture: where the Orchestrator sits

The full design has 8 layers. The orchestrator is layer 3, the core
that every request passes through.

1. **Interfaces**: chat, voice, messaging apps, proactive notifications.
2. **Gateway and Security**: authentication, sessions, OAuth for external accounts, no stored passwords.
3. **Agent Core (this step)**: Router, Planner, Executor, Reflector, and one Agent State that is checkpointed so long tasks survive interruptions. This step ships that core as a plain, testable controller loop. The LangGraph state machine version builds on the same state, plan, and tool contracts.
4. **Model Layer**: a main model for reasoning, a light model for routing, embeddings for memory, speech models for voice, all behind a provider strategy.
5. **Tools Layer**: a central registry. Each tool has a description, a JSON input schema, a permission level, and timeout and retry rules. Tools connect by direct function calling or by MCP servers.
6. **Skills Layer**: descriptive manifests on top of tools. A skill defines its goal, triggers, allowed tools, steps, approval points, and output shape. Adding a skill never edits the core.
7. **Memory and Knowledge**: working memory for the current task, episodic memory for events and decisions, a permanent profile of the user, and procedural memory for how the user likes work done. Plus semantic search over the user's files.
8. **Proactivity and Infrastructure**: a scheduler and event triggers with quiet hours and importance ranking, Postgres with pgvector, Redis for queues, and full observability and evals on every run.

## The execution cycle, in code

`AgentOrchestrator.run(request)`:

1. Understand Intent: `intent.py` produces an `Intent`.
2. Create Plan: `planner.py` produces ordered `PlanStep` items, each bound to a registered tool.
3. Choose Tool: the registry resolves the step's tool. Unknown tools stop the run with `ask_user`.
4. Permission check: `permissions.py` decides whether the tool runs automatically or needs the approval callback.
5. Execute Tool: bounded by the per tool timeout, retried up to the retry limit. Failures are observed, never hidden.
6. Observe Result: every attempt becomes an `Observation` on the state, with its duration.
7. Is the Goal Achieved: `verifier.py` checks that every planned step has a successful observation.
   - Yes: return `completed` with an answer built only from observed results.
   - No: replan while the replan budget remains, otherwise return `ask_user`.

Time and step limits are checked before every step and during retries, so a run can stop with `timeout` or `step_limit_exceeded` at any point, always with the pending step attached.
