from __future__ import annotations

from typing import Any
from uuid import UUID

from sentinelqa.domain.enums import Severity

from .common import anomaly


def evaluate_invariant(
    *,
    run_id: UUID,
    invariant_id: str,
    expected: Any,
    actual: Any,
    route: str,
    action_type: str,
    evidence_refs: tuple[str, ...],
    severity: Severity = Severity.HIGH,
) -> dict[str, Any]:
    passed = expected == actual
    record: dict[str, Any] = {
        "invariant_id": invariant_id,
        "passed": passed,
        "expected": expected,
        "actual": actual,
        "evidence_refs": list(evidence_refs),
    }
    if not passed:
        finding = anomaly(
            run_id=run_id,
            oracle_type=f"invariant:{invariant_id}",
            route=route,
            action_type=action_type,
            signal="business_invariant_violation",
            expected=str(expected),
            actual=str(actual),
            severity=severity,
            evidence_refs=evidence_refs,
            component="atlas.domain",
        )
        record["anomaly"] = finding
        record["anomaly_id"] = str(finding.id)
    return record
