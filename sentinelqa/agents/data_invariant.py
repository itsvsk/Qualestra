from __future__ import annotations

from sentinelqa.agents.base import AgentExecutionContext, successful_result
from sentinelqa.domain.models import AgentResult, AgentTask
from sentinelqa.policy.tool_view import CapabilityScopedToolView


class DataInvariantAgent:
    agent_type = "data_invariant"
    version = "1.0"

    async def execute(
        self,
        task: AgentTask,
        context: AgentExecutionContext,
        tools: CapabilityScopedToolView,
    ) -> AgentResult:
        evaluations = []
        for invariant in context.input_data.get("invariants", []):
            if context.cancellation_requested():
                break
            await context.budget.consume("tool_calls")
            result = await tools.call(
                "evaluate_invariant",
                invariant=invariant,
                observations=context.input_data.get("observations", []),
                entities=context.input_data.get("entities", []),
                target_id=str(context.input_data.get("target_id", "atlas-local")),
            )
            evaluations.append(result)
        failures = [item for item in evaluations if not item.get("passed", False)]
        return successful_result(
            task,
            {
                "invariants_evaluated": len(evaluations),
                "invariants_failed": len(failures),
                "anomalies": failures,
                "anomaly_candidate_ids": [
                    item["anomaly_id"] for item in failures if "anomaly_id" in item
                ],
            },
            tuple(str(item["evidence_ref"]) for item in evaluations if item.get("evidence_ref")),
        )
