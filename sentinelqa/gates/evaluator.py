from __future__ import annotations

import operator
from typing import Any
from uuid import UUID

from sentinelqa.domain.enums import GateOutcome
from sentinelqa.domain.models import GateDecision, GateRuleResult

OPERATORS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}

CONFIDENCE_WEIGHTS = {
    "critical_journey_score": 0.25,
    "changed_feature_coverage": 0.20,
    "api_contract_score": 0.15,
    "verified_defect_score": 0.15,
    "role_and_tenant_score": 0.10,
    "reliability_score": 0.10,
    "accessibility_score": 0.05,
}


class GateEvaluator:
    def evaluate(
        self, run_id: UUID, metrics: dict[str, Any], policy: dict[str, Any]
    ) -> GateDecision:
        results: list[GateRuleResult] = []
        for level, key in (("block", "block_when"), ("warn", "warn_when")):
            for rule in policy.get(key, []):
                metric = rule["metric"]
                operation = rule["operator"]
                if metric not in metrics:
                    actual: Any = None
                    matched = level == "block"
                elif operation not in OPERATORS:
                    raise ValueError(f"unsupported gate operator: {operation}")
                else:
                    actual = metrics[metric]
                    matched = bool(OPERATORS[operation](actual, rule["value"]))
                results.append(
                    GateRuleResult(
                        metric=metric,
                        operator=operation,
                        threshold=rule["value"],
                        actual=actual,
                        matched=matched,
                        level=level,
                    )
                )
        blockers = tuple(item.metric for item in results if item.level == "block" and item.matched)
        warnings = [item for item in results if item.level == "warn" and item.matched]
        outcome = (
            GateOutcome.BLOCK if blockers else GateOutcome.WARN if warnings else GateOutcome.PASS
        )
        confidence = sum(
            float(metrics.get(metric, 0)) * weight for metric, weight in CONFIDENCE_WEIGHTS.items()
        )
        return GateDecision(
            run_id=run_id,
            outcome=outcome,
            rules=tuple(results),
            metrics=metrics,
            blockers=blockers,
            release_confidence=min(1, max(0, confidence)),
        )
