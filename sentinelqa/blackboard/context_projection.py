from __future__ import annotations

from copy import deepcopy
from typing import Any

ALLOWED_CONTEXT_FIELDS: dict[str, frozenset[str]] = {
    "change_impact": frozenset(
        {
            "changed_files",
            "changed_operations",
            "coverage_gaps",
            "feature_map",
            "risk_history",
            "critical_journeys",
        }
    ),
    "ui_explorer": frozenset(
        {
            "run_id",
            "charter",
            "persona",
            "browser",
            "browser_settings",
            "graph",
            "seed",
            "allowed_origin",
            "allowed_origins",
            "base_url",
            "target_id",
        }
    ),
    "api_explorer": frozenset(
        {"charter", "openapi", "resource_pool", "seed", "target_id", "persona"}
    ),
    "data_invariant": frozenset({"entities", "observations", "invariants", "target_id"}),
    "verification": frozenset(
        {"anomaly", "evidence_index", "replay_steps", "replay_type", "seed", "target_id"}
    ),
    "defect_triage": frozenset({"verification", "anomaly", "evidence_index", "defect_history"}),
}


def project_context(agent_type: str, source: dict[str, Any]) -> dict[str, Any]:
    allowed = ALLOWED_CONTEXT_FIELDS[agent_type]
    return deepcopy({key: value for key, value in source.items() if key in allowed})
