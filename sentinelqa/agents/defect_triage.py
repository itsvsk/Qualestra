from __future__ import annotations

from sentinelqa.agents.base import AgentExecutionContext, successful_result
from sentinelqa.domain.models import AgentResult, AgentTask
from sentinelqa.policy.tool_view import CapabilityScopedToolView


class DefectTriageAgent:
    agent_type = "defect_triage"
    version = "1.0"

    async def execute(
        self,
        task: AgentTask,
        context: AgentExecutionContext,
        tools: CapabilityScopedToolView,
    ) -> AgentResult:
        del tools
        verification = context.input_data.get("verification", {})
        if not verification.get("verified", False):
            return successful_result(task, {"disposition": "review_observation"})
        anomaly = context.input_data.get("anomaly", {})
        signature = "|".join(
            str(anomaly.get(key, "unknown"))
            for key in ("oracle_type", "route", "action_type", "signal", "suspected_component")
        )
        duplicates = {
            item.get("signature") for item in context.input_data.get("defect_history", [])
        }
        return successful_result(
            task,
            {
                "disposition": "duplicate" if signature in duplicates else "new_defect",
                "signature": signature,
                "confidence": verification["confidence"],
            },
        )
