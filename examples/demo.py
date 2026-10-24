"""Run the orchestrator end to end.

    python examples/demo.py

Shows three runs:
1. A normal request that completes through the full cycle.
2. A request whose plan needs an external effect tool, which stops
   and asks the user because no approval callback is configured.
3. The same request with approval granted, which completes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from personal_assistant_agent.orchestrator import (
    AgentOrchestrator,
    PermissionLevel,
    Plan,
    PlanStep,
    StaticPlanner,
    Tool,
    default_registry,
)


def show(title, result):
    print(f"\n=== {title} ===")
    print(f"status : {result.status.value}")
    print(f"answer : {result.answer}")
    print(f"reason : {result.reason}")
    print("trace  :")
    for event in result.state.trace:
        print(f"  - {event}")


def main():
    # 1. Full cycle, completes
    agent = AgentOrchestrator(registry=default_registry())
    show("calculate", agent.run("calculate 19 * 3 + 1"))

    # 2 and 3. Permission gate on an external effect tool
    registry = default_registry()
    registry.register(
        Tool(
            name="send_email",
            description="Send an email to a recipient.",
            permission=PermissionLevel.EXTERNAL_EFFECT,
            handler=lambda args: f"email sent to {args.get('to', 'unknown')}",
        )
    )
    plan = Plan(
        goal="send the summary",
        steps=[
            PlanStep(
                id="s1",
                description="send email",
                tool_name="send_email",
                tool_args={"to": "team@example.com"},
            )
        ],
    )

    gated = AgentOrchestrator(registry=registry, planner=StaticPlanner(plan))
    show("send email, no approval configured", gated.run("send the summary"))

    approved = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        approval_callback=lambda step, tool: True,
    )
    show("send email, approval granted", approved.run("send the summary"))


if __name__ == "__main__":
    main()
