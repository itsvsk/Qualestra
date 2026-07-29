from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FailureClass(StrEnum):
    PRODUCT_DEFECT = "product_defect"
    ENVIRONMENT_FLAKE = "environment_flake"
    TOOL_FAILURE = "tool_failure"
    NON_REPRODUCIBLE = "non_reproducible"


@dataclass(frozen=True, slots=True)
class HistoricalSignal:
    feature: str
    verified_defects: int = 0
    failed_attempts: int = 0
    successful_attempts: int = 0
    coverage_gap: bool = False

    @property
    def priority_boost(self) -> float:
        attempts = self.failed_attempts + self.successful_attempts
        failure_rate = self.failed_attempts / attempts if attempts else 0.0
        return min(
            0.35,
            self.verified_defects * 0.08 + failure_rate * 0.15 + (0.12 if self.coverage_gap else 0),
        )


@dataclass(slots=True)
class ExplorationMemory:
    signals: dict[str, HistoricalSignal] = field(default_factory=dict)

    def restore(self, payload: list[dict[str, Any]]) -> None:
        for item in payload:
            signal = HistoricalSignal(
                feature=str(item["feature"]),
                verified_defects=int(item.get("verified_defects", 0)),
                failed_attempts=int(item.get("failed_attempts", 0)),
                successful_attempts=int(item.get("successful_attempts", 0)),
                coverage_gap=bool(item.get("coverage_gap", False)),
            )
            self.signals[signal.feature] = signal

    def risk_history(self) -> dict[str, float]:
        return {
            feature: min(1.0, 0.5 + signal.priority_boost)
            for feature, signal in sorted(self.signals.items())
        }


def classify_attempts(attempts: list[dict[str, Any]]) -> FailureClass:
    if not attempts or not any(bool(item.get("failed")) for item in attempts):
        return FailureClass.NON_REPRODUCIBLE
    failures = [item for item in attempts if item.get("failed")]
    if any(item.get("tool_error") for item in failures):
        return FailureClass.TOOL_FAILURE
    if any(item.get("environment_error") for item in failures):
        return FailureClass.ENVIRONMENT_FLAKE
    reproduced = sum(bool(item.get("same_signature")) for item in failures)
    if reproduced >= 2:
        return FailureClass.PRODUCT_DEFECT
    return FailureClass.NON_REPRODUCIBLE
