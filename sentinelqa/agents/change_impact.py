from __future__ import annotations

from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Any

from sentinelqa.agents.base import AgentExecutionContext, successful_result
from sentinelqa.config import load_yaml
from sentinelqa.domain.models import AgentResult, AgentTask
from sentinelqa.policy.tool_view import CapabilityScopedToolView

RISK_MAP = {
    "authorization": 1.0,
    "tenant": 1.0,
    "plan": 0.9,
    "export": 0.85,
    "audit": 0.8,
    "work_item": 0.8,
    "project": 0.7,
    "web": 0.6,
}


def _feature_rules(configuration: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not configuration:
        return [
            {"id": key, "patterns": [f"*{key}*"], "api_prefixes": [], "risk": value}
            for key, value in RISK_MAP.items()
        ]
    return list(configuration.get("features", []))


def charter_for_files(
    changed_files: list[str],
    *,
    configuration: dict[str, Any] | None = None,
    changed_operations: list[str] | None = None,
    risk_history: dict[str, float] | None = None,
    coverage_gaps: list[str] | None = None,
) -> dict[str, Any]:
    rules = _feature_rules(configuration)
    history = risk_history or {}
    impacts: list[dict[str, Any]] = []
    for path in sorted(set(changed_files)):
        normalized = str(PurePosixPath(path)).lower()
        matched = [
            rule
            for rule in rules
            if any(
                fnmatch(normalized, str(pattern).lower()) for pattern in rule.get("patterns", [])
            )
        ]
        feature = str(matched[0]["id"]) if matched else "unknown"
        base_risk = max((float(item.get("risk", 0.5)) for item in matched), default=0.5)
        impacts.append(
            {
                "feature": feature,
                "classification": "direct" if matched else "unknown",
                "risk_score": min(1.0, max(base_risk, float(history.get(feature, 0)))),
                "evidence": [path],
            }
        )
    for operation in sorted(set(changed_operations or [])):
        normalized = operation.lower()
        matched = [
            rule
            for rule in rules
            if any(prefix.lower() in normalized for prefix in rule.get("api_prefixes", []))
        ]
        feature = str(matched[0]["id"]) if matched else "unknown_api"
        impacts.append(
            {
                "feature": feature,
                "classification": "direct" if matched else "unknown",
                "risk_score": min(
                    1.0,
                    max(
                        max((float(item.get("risk", 0.6)) for item in matched), default=0.6),
                        float(history.get(feature, 0)),
                    ),
                ),
                "evidence": [operation],
            }
        )
    if not impacts:
        impacts.append(
            {
                "feature": "critical_smoke",
                "classification": "unknown",
                "risk_score": 0.8,
                "evidence": ["no diff supplied"],
            }
        )
    features = sorted({impact["feature"] for impact in impacts})
    adjacent = sorted(
        {
            str(item)
            for rule in rules
            if str(rule.get("id")) in features
            for item in rule.get("adjacent", [])
            if str(item) not in features
        }
    )
    gaps = sorted(set(coverage_gaps or []))
    return {
        "schema_version": "1.0",
        "impacts": impacts,
        "task_slices": [
            {"agent_type": agent, "features": features}
            for agent in ("ui_explorer", "api_explorer", "data_invariant")
        ],
        "budget_allocation": {"direct": 0.6, "adjacent": 0.25, "smoke": 0.15},
        "adjacent_features": adjacent,
        "coverage_gaps": gaps,
        "uncertainties": [
            impact["feature"] for impact in impacts if impact["classification"] == "unknown"
        ],
    }


class ChangeImpactAgent:
    agent_type = "change_impact"
    version = "1.0"

    async def execute(
        self,
        task: AgentTask,
        context: AgentExecutionContext,
        tools: CapabilityScopedToolView,
    ) -> AgentResult:
        del tools
        configuration = dict(
            context.input_data.get("feature_map") or load_yaml("config/impact-map.yaml")
        )
        charter = charter_for_files(
            list(context.input_data.get("changed_files", [])),
            configuration=configuration,
            changed_operations=list(context.input_data.get("changed_operations", [])),
            risk_history=dict(context.input_data.get("risk_history", {})),
            coverage_gaps=list(context.input_data.get("coverage_gaps", [])),
        )
        return successful_result(task, {"charter": charter})
