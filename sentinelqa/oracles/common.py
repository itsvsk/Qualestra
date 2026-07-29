from uuid import UUID

from sentinelqa.domain.enums import Severity
from sentinelqa.domain.models import Anomaly


def anomaly(
    *,
    run_id: UUID,
    oracle_type: str,
    route: str,
    action_type: str,
    signal: str,
    expected: str,
    actual: str,
    severity: Severity,
    evidence_refs: tuple[str, ...],
    component: str = "unknown",
) -> Anomaly:
    if not evidence_refs:
        raise ValueError("anomalies must link to raw evidence")
    return Anomaly(
        run_id=run_id,
        oracle_type=oracle_type,
        route=route,
        action_type=action_type,
        signal=signal,
        expected=expected,
        actual=actual,
        severity_hint=severity,
        evidence_refs=evidence_refs,
        suspected_component=component,
    )
