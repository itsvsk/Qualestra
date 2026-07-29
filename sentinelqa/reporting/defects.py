from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sentinelqa.domain.enums import Severity
from sentinelqa.domain.models import Anomaly, Defect, VerificationResult

from .redaction import redact_report_value


def build_defect(
    *,
    run_id: UUID,
    anomaly: Anomaly,
    verification: VerificationResult,
    persona: str,
    affected_area: str,
    suggested_investigation: str,
) -> Defect:
    if not verification.verified:
        raise ValueError("only independently verified findings become defects")
    return Defect(
        run_id=run_id,
        title=f"{anomaly.signal.replace('_', ' ').title()} on {anomaly.route}",
        severity=Severity(anomaly.severity_hint),
        confidence=verification.confidence,
        affected_area=affected_area,
        signature=anomaly.signature,
        persona=persona,
        preconditions=("Atlas is reset to the recorded deterministic seed",),
        steps_to_reproduce=verification.minimized_steps,
        expected=anomaly.expected,
        actual=anomaly.actual,
        verification={"attempts": verification.attempts, "matched": verification.matched},
        evidence=tuple(
            {"kind": "evidence", "artifact_id": reference}
            for reference in verification.evidence_refs
        ),
        suspected_component=anomaly.suspected_component,
        suggested_investigation=suggested_investigation,
    )


def developer_handoff(defects: list[Defect], commit_sha: str | None = None) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        redact_report_value(
            {
                "schema_version": "1.0",
                "commit_sha": commit_sha,
                "verified_defects": [defect.model_dump(mode="json") for defect in defects],
            }
        ),
    )
