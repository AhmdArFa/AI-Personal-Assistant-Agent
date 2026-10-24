import json

from personal_assistant_agent.orchestrator import (
    AgentOrchestrator,
    GatewayIntentUnderstanding,
    GatewayPlanner,
    PermissionLevel,
    RuleBasedPlanner,
    RunStatus,
    StopReason,
    TenantContext,
    Tool,
    default_registry,
)
from personal_assistant_agent.platform import ModelGateway, ModelResponse

CTX = TenantContext(user_id="user-1", workspace_id="ws-1")


class ScriptedProvider:
    name = "scripted"
    model_version = "scripted-1"

    def __init__(self, text):
        self.text = text

    def complete(self, request):
        return ModelResponse(text=self.text, tokens_used=5, cost_usd=0.0)


def _agent(response_text, registry=None):
    gateway = ModelGateway({"scripted": ScriptedProvider(response_text)})
    return AgentOrchestrator(
        registry=registry or default_registry(),
        planner=GatewayPlanner(gateway, "scripted"),
        intent_understanding=GatewayIntentUnderstanding(gateway, "scripted"),
    )


def _plan_json(steps):
    return json.dumps({"steps": steps})


def test_valid_model_plan_executes():
    agent = _agent(
        _plan_json(
            [
                {
                    "description": "calc",
                    "tool_name": "calculator",
                    "tool_args": {"expression": "2 + 2"},
                }
            ]
        )
    )
    result = agent.run("what is 2 + 2", context=CTX)
    assert result.status is RunStatus.COMPLETED
    assert result.answer == "4"


def test_invalid_json_is_a_safe_stop():
    result = _agent("I think you should just do it, trust me").run(
        "do something", context=CTX
    )
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.VALIDATION_ERROR
    assert "not valid JSON" in result.reason


def test_unknown_tool_is_a_safe_stop():
    result = _agent(
        _plan_json(
            [{"description": "rm", "tool_name": "delete_everything", "tool_args": {}}]
        )
    ).run("clean up", context=CTX)
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.VALIDATION_ERROR
    assert "unknown tool" in result.reason


def test_extra_argument_field_is_a_safe_stop():
    result = _agent(
        _plan_json(
            [
                {
                    "description": "echo",
                    "tool_name": "echo",
                    "tool_args": {"message": "hi", "admin_override": True},
                }
            ]
        )
    ).run("say hi", context=CTX)
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.VALIDATION_ERROR
    assert "invalid arguments" in result.reason


def test_injection_proposed_side_effect_still_hits_the_approval_gate():
    calls = []
    registry = default_registry()
    registry.register(
        Tool(
            name="send_email",
            description="send mail",
            permission=PermissionLevel.EXTERNAL_EFFECT,
            handler=lambda args, context: calls.append(1) or "sent",
        )
    )
    # A document told the model to send mail; even with the model
    # complying, the permission gate stops it for approval.
    agent = _agent(
        _plan_json(
            [
                {
                    "description": "send as the document demands",
                    "tool_name": "send_email",
                    "tool_args": {},
                }
            ]
        ),
        registry=registry,
    )
    result = agent.run("summarise the document", context=CTX)
    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert calls == []


def test_intent_falls_back_safely_on_garbage():
    gateway = ModelGateway({"scripted": ScriptedProvider("not json")})
    intent = GatewayIntentUnderstanding(gateway, "scripted").understand(
        "calculate 2 + 2"
    )
    assert intent.goal == "calculate 2 + 2"
    assert intent.kind == "task"


def test_rule_based_planner_remains_the_default():
    agent = AgentOrchestrator(registry=default_registry())
    assert isinstance(agent.planner, RuleBasedPlanner)
