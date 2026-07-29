from sentinelqa.domain.enums import Severity
from sentinelqa.domain.models import Anomaly, Observation

from .common import anomaly


def network_anomalies(observation: Observation) -> list[Anomaly]:
    evidence = tuple(
        ref
        for ref in (
            observation.evidence.trace_chunk_artifact_id,
            observation.evidence.screenshot_artifact_id,
            observation.evidence.dom_artifact_id,
        )
        if ref
    )
    output = []
    for response in observation.signals.responses:
        status = int(response.get("status", 0))
        if status >= 500:
            output.append(
                anomaly(
                    run_id=observation.run_id,
                    oracle_type="explicit_network",
                    route=observation.page.route,
                    action_type=str(response.get("method", "request")).lower(),
                    signal="http_5xx",
                    expected="response status below 500",
                    actual=f"{status} {response.get('url', '')}",
                    severity=Severity.HIGH,
                    evidence_refs=evidence,
                    component="atlas.api",
                )
            )
    for request in observation.signals.failed_requests:
        output.append(
            anomaly(
                run_id=observation.run_id,
                oracle_type="explicit_network",
                route=observation.page.route,
                action_type=str(request.get("method", "request")).lower(),
                signal="failed_request",
                expected="request completes",
                actual=str(request.get("failure", "failed")),
                severity=Severity.MEDIUM,
                evidence_refs=evidence,
                component="network",
            )
        )
    return output
