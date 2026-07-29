from typing import Any
from uuid import UUID

from sentinelqa.domain.enums import Severity
from sentinelqa.domain.models import Anomaly

from .common import anomaly


def metamorphic_anomaly(
    *,
    run_id: UUID,
    relation: str,
    route: str,
    before: Any,
    after: Any,
    evidence_refs: tuple[str, ...],
    severity: Severity = Severity.HIGH,
) -> Anomaly | None:
    if before == after:
        return None
    return anomaly(
        run_id=run_id,
        oracle_type="metamorphic",
        route=route,
        action_type=relation,
        signal=f"metamorphic_{relation}_failed",
        expected=f"value stable across {relation}",
        actual=f"{before!r} became {after!r}",
        severity=severity,
        evidence_refs=evidence_refs,
        component="atlas.persistence",
    )
