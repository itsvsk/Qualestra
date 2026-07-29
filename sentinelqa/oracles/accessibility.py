from typing import Any
from uuid import UUID

from sentinelqa.domain.enums import Severity
from sentinelqa.domain.models import Anomaly

from .common import anomaly


def accessibility_anomalies(
    *, run_id: UUID, route: str, violations: list[dict[str, Any]], evidence_refs: tuple[str, ...]
) -> list[Anomaly]:
    return [
        anomaly(
            run_id=run_id,
            oracle_type="accessibility",
            route=route,
            action_type="axe_scan",
            signal=str(item.get("id", "accessibility_violation")),
            expected="no critical or serious accessibility violations",
            actual=str(item.get("help", item)),
            severity=Severity.HIGH if item.get("impact") == "critical" else Severity.MEDIUM,
            evidence_refs=evidence_refs,
            component="atlas.web.accessibility",
        )
        for item in violations
        if item.get("impact") in {"critical", "serious"}
    ]
