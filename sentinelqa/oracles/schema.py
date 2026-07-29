from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator

from sentinelqa.domain.enums import Severity
from sentinelqa.domain.models import Anomaly

from .common import anomaly


def schema_anomalies(
    *,
    run_id: UUID,
    route: str,
    payload: Any,
    schema: dict[str, Any],
    evidence_refs: tuple[str, ...],
) -> list[Anomaly]:
    return [
        anomaly(
            run_id=run_id,
            oracle_type="explicit_schema",
            route=route,
            action_type="api_request",
            signal="response_schema_violation",
            expected="payload conforms to OpenAPI response schema",
            actual=error.message,
            severity=Severity.HIGH,
            evidence_refs=evidence_refs,
            component="atlas.api.contract",
        )
        for error in Draft202012Validator(schema).iter_errors(payload)
    ]
