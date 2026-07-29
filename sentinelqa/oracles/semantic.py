from uuid import UUID

from sentinelqa.domain.enums import Severity
from sentinelqa.domain.models import Anomaly

from .common import anomaly


def semantic_anomaly(
    *,
    run_id: UUID,
    route: str,
    signal: str,
    expected: str,
    actual: str,
    evidence_refs: tuple[str, ...],
    deterministic_support: bool = False,
) -> Anomaly:
    return anomaly(
        run_id=run_id,
        oracle_type="semantic",
        route=route,
        action_type="observe",
        signal=signal,
        expected=expected,
        actual=actual,
        severity=Severity.HIGH if deterministic_support else Severity.MEDIUM,
        evidence_refs=evidence_refs,
        component="atlas.web.usability",
    )
