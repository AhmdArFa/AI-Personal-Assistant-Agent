"""HTTP API surface (Blueprint section 10).

One endpoint in this slice: task status. Identity arrives as
server session headers (X-User-Id, X-Workspace-Id) standing in for
the identity provider chosen in decision D1. Isolation rule: a
task that belongs to another tenant is indistinguishable from an
unknown task (404), so existence never leaks.

The response is deliberately minimal: status, current step,
completed steps, and a safe error summary. No prompts, no secrets,
no raw traces.
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException

from .orchestrator.checkpoints import CheckpointStore


def create_app(checkpoint_store: CheckpointStore) -> FastAPI:
    app = FastAPI(title="AI Personal Assistant Agent API", version="0.1.0")

    @app.get("/api/v1/tasks/{task_id}")
    def task_status(
        task_id: str,
        x_user_id: str = Header(...),
        x_workspace_id: str = Header(...),
    ) -> dict:
        state = checkpoint_store.load(task_id)
        if (
            state is None
            or state.tenant is None
            or state.tenant.user_id != x_user_id
            or state.tenant.workspace_id != x_workspace_id
        ):
            raise HTTPException(status_code=404, detail="task not found")

        completed_steps: list[str] = []
        for obs in state.observations:
            if obs.success and obs.step_id not in completed_steps:
                completed_steps.append(obs.step_id)

        current_step = None
        if state.plan is not None:
            for step in state.plan.steps:
                if step.id not in completed_steps:
                    current_step = step.id
                    break

        error_summary = ""
        if state.status.value == "failed":
            failures = [
                obs.error
                for obs in state.observations
                if not obs.success and obs.error
            ]
            error_summary = failures[-1] if failures else ""

        return {
            "task_id": state.task_id,
            "status": state.status.value,
            "current_step": current_step,
            "completed_steps": completed_steps,
            "steps_executed": state.steps_executed,
            "error_summary": error_summary,
        }

    return app
