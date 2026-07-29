from __future__ import annotations

from collections.abc import Mapping

from sentinelqa.orchestration.agent_registry import AgentRegistry
from sentinelqa.orchestration.agent_runtime import AgentRuntime, RuntimeBlackboard
from sentinelqa.policy.tool_view import Tool


def build_runtime(
    blackboard: RuntimeBlackboard,
    tools: Mapping[str, Tool],
) -> AgentRuntime:
    registry = AgentRegistry.from_file("config/agents.yaml")
    return AgentRuntime(registry, blackboard, tools)
