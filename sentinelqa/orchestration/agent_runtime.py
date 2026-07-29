from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Protocol, cast

from jsonschema import Draft202012Validator

from sentinelqa.agents.base import AgentExecutionContext
from sentinelqa.config import load_yaml
from sentinelqa.domain.events import EventEnvelope
from sentinelqa.domain.models import AgentResult, AgentTask
from sentinelqa.policy.budgets import AtomicBudget, BudgetExhausted
from sentinelqa.policy.tool_view import CapabilityScopedToolView, Tool

from .agent_registry import AgentRegistry


class InvalidAgentResult(ValueError):
    pass


class RuntimeBlackboard(Protocol):
    async def get_context(self, ref: str) -> dict[str, Any]: ...

    async def commit_result(self, result: AgentResult, event: EventEnvelope) -> None: ...


class AgentRuntime:
    def __init__(
        self,
        registry: AgentRegistry,
        blackboard: RuntimeBlackboard,
        tools: Mapping[str, Tool],
    ) -> None:
        self.registry = registry
        self.blackboard = blackboard
        self.tools = tools
        self._cancelled_runs: set[Any] = set()

    async def execute_task(self, task: AgentTask) -> AgentResult:
        manifest = self.registry.manifest(str(task.agent_type), task.agent_version)
        if task.budget != manifest.budget:
            for name in task.budget.model_fields:
                if getattr(task.budget, name) > getattr(manifest.budget, name):
                    raise ValueError("task budget exceeds manifest")
        context = AgentExecutionContext(
            run_id=task.run_id,
            task_id=task.task_id,
            agent_type=str(task.agent_type),
            agent_version=task.agent_version,
            input_data=await self.blackboard.get_context(task.input_ref),
            budget=AtomicBudget(task.budget),
            cancellation_requested=lambda: task.run_id in self._cancelled_runs,
        )
        view = CapabilityScopedToolView(manifest.capabilities, self.tools)
        agent = self.registry.instantiate(str(task.agent_type), task.agent_version)
        try:
            async with asyncio.timeout(task.budget.max_seconds):
                result = cast(AgentResult, await agent.execute(task, context, view))
        except BudgetExhausted:
            result = AgentResult(
                task_id=task.task_id,
                agent_type=task.agent_type,
                outcome="budget_exhausted",
                result_ref=f"blackboard://results/{task.task_id}",
            )
        self._validate_result(result, manifest.result_schema)
        event = EventEnvelope(
            event_type="agent.result.committed",
            run_id=task.run_id,
            correlation_id=task.task_id,
            producer="qa-worker",
            payload={"agent_type": str(task.agent_type), "outcome": result.outcome},
        )
        await self.blackboard.commit_result(result, event)
        return result

    def _validate_result(self, result: AgentResult, schema_path: str) -> None:
        schema = load_yaml(schema_path)
        errors = list(Draft202012Validator(schema).iter_errors(result.model_dump(mode="json")))
        if errors:
            raise InvalidAgentResult("; ".join(error.message for error in errors))

    async def cancel_run(self, run_id: Any) -> None:
        self._cancelled_runs.add(run_id)
