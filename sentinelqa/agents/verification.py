from __future__ import annotations

from sentinelqa.agents.base import AgentExecutionContext, successful_result
from sentinelqa.domain.models import AgentResult, AgentTask
from sentinelqa.exploration.memory import FailureClass, classify_attempts
from sentinelqa.policy.tool_view import CapabilityScopedToolView
from sentinelqa.verification.confidence import confidence_score


class VerificationAgent:
    agent_type = "verification"
    version = "1.0"

    async def execute(
        self,
        task: AgentTask,
        context: AgentExecutionContext,
        tools: CapabilityScopedToolView,
    ) -> AgentResult:
        package = context.input_data
        target_id = str(package.get("target_id", "atlas-local"))
        attempts = []
        for _attempt in range(2):
            await context.budget.consume("tool_calls", 2)
            await tools.call("reset_test_target", target_id=target_id)
            await tools.call(
                "seed_test_target", target_id=target_id, seed=package.get("seed", 41021)
            )
            capability = "replay_api" if package.get("replay_type") == "api" else "replay_ui"
            await context.budget.consume("tool_calls")
            attempts.append(
                await tools.call(
                    capability,
                    steps=package.get("replay_steps", []),
                    target_id=target_id,
                )
            )
        matched = sum(bool(item.get("matched")) for item in attempts)
        failure_class = classify_attempts(
            [
                {
                    "failed": bool(item.get("matched")),
                    "same_signature": bool(item.get("matched")),
                    "environment_error": float(item.get("instability", 0)) >= 0.5,
                    "tool_error": bool(item.get("tool_error", False)),
                }
                for item in attempts
            ]
        )
        rate = matched / len(attempts)
        confidence = confidence_score(
            reproduction_rate=rate,
            deterministic_signal_strength=float(package.get("deterministic_signal_strength", 1)),
            invariant_strength=float(package.get("invariant_strength", 0.8)),
            cross_layer_confirmation=float(package.get("cross_layer_confirmation", 0.5)),
            clean_environment_confirmation=1,
            cross_browser_confirmation=float(package.get("cross_browser_confirmation", 0)),
            environment_instability=max(float(item.get("instability", 0)) for item in attempts),
        )
        return successful_result(
            task,
            {
                "anomaly_id": package.get("anomaly", {}).get("id"),
                "attempts": len(attempts),
                "matched": matched,
                "confidence": confidence,
                "verified": confidence >= 0.70
                and matched == len(attempts)
                and failure_class == FailureClass.PRODUCT_DEFECT,
                "failure_class": str(failure_class),
                "minimized_steps": package.get("replay_steps", []),
            },
            tuple(str(ref) for item in attempts for ref in item.get("evidence_refs", [])),
        )
