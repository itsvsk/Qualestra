from __future__ import annotations

from sentinelqa.domain.models import Anomaly


def deduplicate_anomalies(anomalies: list[Anomaly]) -> list[Anomaly]:
    output: dict[str, Anomaly] = {}
    for anomaly in sorted(anomalies, key=lambda item: str(item.id)):
        output.setdefault(anomaly.signature, anomaly)
    return list(output.values())
