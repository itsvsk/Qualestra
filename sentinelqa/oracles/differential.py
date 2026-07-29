from typing import Any
from uuid import UUID

from sentinelqa.domain.enums import Severity
from sentinelqa.domain.models import Anomaly

from .common import anomaly


def differential_anomaly(
    *,
    run_id: UUID,
    route: str,
    left_name: str,
    left: Any,
    right_name: str,
    right: Any,
    evidence_refs: tuple[str, ...],
    severity: Severity = Severity.HIGH,
) -> Anomaly | None:
    if left == right:
        return None
    return anomaly(
        run_id=run_id,
        oracle_type="differential",
        route=route,
        action_type="compare",
        signal=f"{left_name}_versus_{right_name}",
        expected=f"{left_name} equals {right_name}",
        actual=f"{left!r} != {right!r}",
        severity=severity,
        evidence_refs=evidence_refs,
        component="atlas.cross_layer",
    )
