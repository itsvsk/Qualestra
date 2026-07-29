from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sentinelqa.domain.models import AgentResult, AgentTask
from sentinelqa.policy.budgets import AtomicBudget
from sentinelqa.policy.tool_view import CapabilityScopedToolView


@dataclass(frozen=True, slots=True)
class AgentExecutionContext:
    run_id: UUID
    task_id: UUID
    agent_type: str
    agent_version: str
    input_data: dict[str, Any]
    budget: AtomicBudget
    cancellation_requested: Callable[[], bool]


class LogicalAgent(Protocol):
    agent_type: str
    version: str

    async def execute(
        self,
        task: AgentTask,
        context: AgentExecutionContext,
        tools: CapabilityScopedToolView,
    ) -> AgentResult: ...


def successful_result(
    task: AgentTask,
    summary: dict[str, Any],
    evidence_refs: tuple[str, ...] = (),
) -> AgentResult:
    return AgentResult(
        task_id=task.task_id,
        agent_type=task.agent_type,
        outcome="succeeded",
        result_ref=f"blackboard://results/{task.task_id}",
        summary=summary,
        evidence_refs=evidence_refs,
    )
