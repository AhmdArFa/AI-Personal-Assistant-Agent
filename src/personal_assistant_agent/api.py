"""HTTP API surface (Blueprint section 10, work package W2).

Endpoints:
- GET  /api/v1/health
- POST /api/v1/tasks                     start a task
- GET  /api/v1/tasks/{task_id}           task status
- POST /api/v1/tasks/{task_id}/cancel    cancel a running or waiting task
- POST /api/v1/tasks/{task_id}/approve   decide a pending approval

Identity rules (both modes):
- Platform mode (identity provider wired): the Bearer token proves
  who the caller is, the workspace is selected by header, and its
  authority comes from the membership tables only.
- Header mode (no provider): X-User-Id and X-Workspace-Id stand in
  for a server session, for local development only.

Isolation rule on every task route: a task that belongs to another
tenant, a workspace the caller is not a member of, and an unknown
task are indistinguishable (404), so existence never leaks.
Responses never contain prompts, secrets, or raw traces.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .orchestrator import (
    AgentOrchestrator,
    RunStatus,
    TenantContext,
    default_registry,
)
from .orchestrator.checkpoints import CheckpointStore
from .platform.identity import (
    AuthenticationError,
    IdentityProvider,
    get_or_create_user,
)
from .platform.tenancy import TenancyStore


class CreateTaskBody(BaseModel):
    request: str = Field(min_length=1, max_length=4000)
    task_id: str | None = None


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


class ApproveBody(BaseModel):
    approved: bool
    approval_id: str | None = None


def create_app(
    checkpoint_store: CheckpointStore,
    tenancy_store: TenancyStore | None = None,
    identity_provider: IdentityProvider | None = None,
    orchestrator: AgentOrchestrator | None = None,
    gateway=None,
    chat_provider: str = "stub",
    conversation_store=None,
    usage_limits=None,
) -> FastAPI:
    app = FastAPI(title="AI Personal Assistant Agent API", version="0.2.0")
    platform_mode = identity_provider is not None and tenancy_store is not None

    def _orchestrator() -> AgentOrchestrator:
        nonlocal orchestrator
        if orchestrator is None:
            orchestrator = AgentOrchestrator(
                registry=default_registry(),
                checkpoint_store=checkpoint_store,
            )
        return orchestrator

    def _caller(
        x_user_id: str, x_workspace_id: str, authorization: str
    ) -> tuple[str, str]:
        """Resolve (user_id, workspace_id) or raise 401 or 404."""
        if platform_mode:
            assert identity_provider is not None and tenancy_store is not None
            if not authorization.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="missing token")
            try:
                identity = identity_provider.authenticate(
                    authorization.removeprefix("Bearer ")
                )
            except AuthenticationError:
                raise HTTPException(status_code=401, detail="invalid token") from None
            user = get_or_create_user(tenancy_store, identity)
            if not x_workspace_id or not tenancy_store.is_member(
                x_workspace_id, user.user_id
            ):
                raise HTTPException(status_code=404, detail="task not found")
            return user.user_id, x_workspace_id
        if not x_user_id or not x_workspace_id:
            raise HTTPException(status_code=404, detail="task not found")
        return x_user_id, x_workspace_id

    def _owned_state(task_id: str, user_id: str, workspace_id: str):
        state = checkpoint_store.load(task_id)
        if (
            state is None
            or state.tenant is None
            or state.tenant.user_id != user_id
            or state.tenant.workspace_id != workspace_id
        ):
            raise HTTPException(status_code=404, detail="task not found")
        return state

    def _result_view(result) -> dict:
        return {
            "task_id": result.state.task_id,
            "status": result.status.value,
            "stop_reason": (result.stop_reason.value if result.stop_reason else None),
            "answer": result.answer,
            "summary": result.summary,
            "error_summary": result.error_summary,
        }

    def _chat_service():
        from .platform import (
            ChatService,
            InMemoryConversationStore,
            ModelGateway,
            StubProvider,
            UsageLedger,
        )

        nonlocal gateway, conversation_store
        if gateway is None:
            gateway = ModelGateway({"stub": StubProvider()})
        if conversation_store is None:
            conversation_store = InMemoryConversationStore()
        return ChatService(
            gateway=gateway,
            provider=chat_provider,
            conversations=conversation_store,
            usage=UsageLedger(),
            limits=usage_limits,
        )

    _chat = None

    def _chat_cached():
        nonlocal _chat
        if _chat is None:
            _chat = _chat_service()
        return _chat

    @app.post("/api/v1/chat")
    def chat(
        body: ChatBody,
        x_user_id: str = Header(default=""),
        x_workspace_id: str = Header(default=""),
        authorization: str = Header(default=""),
    ) -> dict:
        from .platform import UsageLimitExceeded

        user_id, workspace_id = _caller(x_user_id, x_workspace_id, authorization)
        try:
            reply = _chat_cached().chat(
                user_id, workspace_id, body.message, body.conversation_id
            )
        except PermissionError:
            raise HTTPException(status_code=404, detail="task not found") from None
        except UsageLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from None
        return {
            "conversation_id": reply.conversation_id,
            "reply": reply.text,
            "provider": reply.provider,
            "model_version": reply.model_version,
            "tokens_used": reply.tokens_used,
            "cost_usd": reply.cost_usd,
        }

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/v1/tasks", status_code=201)
    def create_task(
        body: CreateTaskBody,
        x_user_id: str = Header(default=""),
        x_workspace_id: str = Header(default=""),
        authorization: str = Header(default=""),
    ) -> dict:
        user_id, workspace_id = _caller(x_user_id, x_workspace_id, authorization)
        result = _orchestrator().run(
            body.request,
            context=TenantContext(user_id=user_id, workspace_id=workspace_id),
            task_id=body.task_id or uuid.uuid4().hex,
        )
        return _result_view(result)

    @app.get("/api/v1/tasks/{task_id}")
    def task_status(
        task_id: str,
        x_user_id: str = Header(default=""),
        x_workspace_id: str = Header(default=""),
        authorization: str = Header(default=""),
    ) -> dict:
        user_id, workspace_id = _caller(x_user_id, x_workspace_id, authorization)
        state = _owned_state(task_id, user_id, workspace_id)

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
                obs.error for obs in state.observations if not obs.success and obs.error
            ]
            error_summary = failures[-1] if failures else ""
        pending = next((a for a in state.approvals if a.status == "pending"), None)
        return {
            "task_id": state.task_id,
            "status": state.status.value,
            "current_step": current_step,
            "completed_steps": completed_steps,
            "steps_executed": state.steps_executed,
            "error_summary": error_summary,
            "pending_approval": (
                {
                    "approval_id": pending.id,
                    "tool_name": pending.tool_name,
                    "tool_args": pending.tool_args,
                    "arguments_hash": pending.arguments_hash,
                }
                if pending is not None
                else None
            ),
        }

    @app.post("/api/v1/tasks/{task_id}/cancel")
    def cancel_task(
        task_id: str,
        x_user_id: str = Header(default=""),
        x_workspace_id: str = Header(default=""),
        authorization: str = Header(default=""),
    ) -> dict:
        user_id, workspace_id = _caller(x_user_id, x_workspace_id, authorization)
        state = _owned_state(task_id, user_id, workspace_id)
        if _orchestrator().cancel_task(task_id):
            return {"task_id": task_id, "status": "cancelling"}
        if state.status is RunStatus.WAITING_FOR_APPROVAL:
            state.status = RunStatus.CANCELLED
            from .orchestrator.checkpoints import serialize_state

            checkpoint_store.save_snapshot(
                task_id,
                _orchestrator().redactor.redact(serialize_state(state)),
            )
            return {"task_id": task_id, "status": "cancelled"}
        return {"task_id": task_id, "status": state.status.value}

    @app.post("/api/v1/tasks/{task_id}/approve")
    def approve_task(
        task_id: str,
        body: ApproveBody,
        x_user_id: str = Header(default=""),
        x_workspace_id: str = Header(default=""),
        authorization: str = Header(default=""),
    ) -> dict:
        user_id, workspace_id = _caller(x_user_id, x_workspace_id, authorization)
        _owned_state(task_id, user_id, workspace_id)
        try:
            result = _orchestrator().decide_approval(
                task_id,
                context=TenantContext(user_id=user_id, workspace_id=workspace_id),
                approved=body.approved,
                approval_id=body.approval_id,
            )
        except PermissionError:
            raise HTTPException(status_code=404, detail="task not found") from None
        except ValueError as exc:
            message = str(exc)
            if "no checkpoint" in message:
                raise HTTPException(status_code=404, detail="task not found") from None
            raise HTTPException(status_code=409, detail=message) from None
        return _result_view(result)

    return app
