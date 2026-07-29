from __future__ import annotations

from typing import Any
from uuid import uuid4

from sentinelqa.agents.base import AgentExecutionContext, successful_result
from sentinelqa.domain.models import ActionRecord, AgentResult, AgentTask, Observation, Transition
from sentinelqa.exploration.candidates import candidates_from_observation
from sentinelqa.exploration.scorer import rank_actions, score_action
from sentinelqa.policy.engine import PolicyEngine
from sentinelqa.policy.tool_view import CapabilityScopedToolView


class UIExplorationAgent:
    agent_type = "ui_explorer"
    version = "1.0"

    async def execute(
        self,
        task: AgentTask,
        context: AgentExecutionContext,
        tools: CapabilityScopedToolView,
    ) -> AgentResult:
        await context.budget.consume("tool_calls")
        raw: Any = await tools.call("observe_ui", context=context.input_data)
        observation = raw if isinstance(raw, Observation) else Observation.model_validate(raw)
        origins = {context.input_data.get("allowed_origin", observation.page.url)}
        policy = PolicyEngine(origins)
        candidates = rank_actions(candidates_from_observation(observation))
        authorized = [
            candidate
            for candidate in candidates
            if policy.authorize(candidate, observation).allowed
        ]
        denied = len(candidates) - len(authorized)
        executed = 0
        evidence = [
            ref
            for ref in (
                observation.evidence.screenshot_artifact_id,
                observation.evidence.trace_chunk_artifact_id,
                observation.evidence.dom_artifact_id,
            )
            if ref
        ]
        observations = [observation.model_dump(mode="json")]
        transitions: list[dict[str, Any]] = []
        accessibility_violations = list(observation.signals.accessibility_violations)
        if authorized and not context.cancellation_requested():
            selected = authorized[0]
            capability = (
                "safe_ui_action" if str(selected.safety_class) == "safe" else "controlled_ui_action"
            )
            if capability in tools.names():
                await context.budget.consume("tool_calls")
                action_result: Any = await tools.call(
                    capability, candidate=selected.model_dump(mode="json")
                )
                executed = 1
                if isinstance(action_result, dict) and "after" in action_result:
                    after = Observation.model_validate(action_result["after"])
                    observations.append(after.model_dump(mode="json"))
                    accessibility_violations.extend(after.signals.accessibility_violations)
                    evidence.extend(
                        ref
                        for ref in (
                            after.evidence.screenshot_artifact_id,
                            after.evidence.trace_chunk_artifact_id,
                            after.evidence.dom_artifact_id,
                        )
                        if ref
                    )
                    action = ActionRecord(
                        action_type=selected.action_type,
                        target_control_id=selected.control_id,
                        safety_class=selected.safety_class,
                        score=score_action(selected),
                        rationale="policy_allowed",
                    )
                    transitions.append(
                        Transition(
                            from_state_id=observation.state_fingerprint,
                            action_id=action.id,
                            to_state_id=after.state_fingerprint,
                            outcome=str(action_result.get("outcome", "succeeded")),
                            duration_ms=int(action_result.get("duration_ms", 0)),
                        ).model_dump(mode="json")
                    )
        unique_accessibility = {
            (str(item.get("id")), str(item.get("selector"))): item
            for item in accessibility_violations
            if item.get("impact") in {"critical", "serious"}
        }
        accessibility_anomalies = [
            {
                "anomaly_id": str(uuid4()),
                "oracle_type": "accessibility",
                "route": observation.page.route,
                "action_type": "accessibility_scan",
                "signal": str(item.get("id", "accessibility_violation")),
                "expected": "no critical or serious accessibility violations",
                "actual": str(item.get("help", item)),
                "severity_hint": "high" if item.get("impact") == "critical" else "medium",
                "evidence_refs": list(dict.fromkeys(evidence)),
                "suspected_component": "target.web.accessibility",
                "replay_steps": [
                    {
                        "type": "accessibility_scan",
                        "context": context.input_data,
                        "expected_signal": str(item.get("id", "accessibility_violation")),
                    }
                ],
            }
            for item in unique_accessibility.values()
        ]
        return successful_result(
            task,
            {
                "states_discovered": 1,
                "transitions_executed": executed,
                "candidates_considered": len(candidates),
                "policy_denials": denied,
                "anomalies": accessibility_anomalies,
                "anomaly_candidate_ids": [item["anomaly_id"] for item in accessibility_anomalies],
                "accessibility_violations": accessibility_violations,
                "observations": observations,
                "transitions": transitions,
            },
            tuple(dict.fromkeys(evidence)),
        )
