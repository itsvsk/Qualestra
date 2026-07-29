from sentinelqa.domain.enums import Severity
from sentinelqa.domain.models import Anomaly, Observation

from .common import anomaly


def console_anomalies(observation: Observation, allowlist: tuple[str, ...] = ()) -> list[Anomaly]:
    evidence = tuple(
        ref
        for ref in (
            observation.evidence.screenshot_artifact_id,
            observation.evidence.trace_chunk_artifact_id,
            observation.evidence.dom_artifact_id,
        )
        if ref
    )
    return [
        anomaly(
            run_id=observation.run_id,
            oracle_type="explicit_console",
            route=observation.page.route,
            action_type="observe",
            signal="uncaught_browser_exception",
            expected="no uncaught console errors",
            actual=message,
            severity=Severity.HIGH,
            evidence_refs=evidence,
            component="atlas.web",
        )
        for message in observation.signals.console_errors
        if not any(item in message for item in allowlist)
    ]
