from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from sentinelqa.domain.models import BudgetUsed

MetricKind = Literal["counter", "gauge", "histogram"]


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    name: str
    kind: MetricKind
    labels: frozenset[str]


METRICS: dict[str, MetricDefinition] = {
    definition.name: definition
    for definition in (
        MetricDefinition("sentinelqa_runs_total", "counter", frozenset({"status", "trigger"})),
        MetricDefinition(
            "sentinelqa_agent_tasks_total",
            "counter",
            frozenset({"agent_type", "outcome"}),
        ),
        MetricDefinition(
            "sentinelqa_agent_task_duration_seconds",
            "histogram",
            frozenset({"agent_type"}),
        ),
        MetricDefinition(
            "sentinelqa_agent_budget_used",
            "histogram",
            frozenset({"agent_type", "budget_kind"}),
        ),
        MetricDefinition(
            "sentinelqa_agent_result_schema_failures_total",
            "counter",
            frozenset({"agent_type"}),
        ),
        MetricDefinition(
            "sentinelqa_agent_capability_denials_total",
            "counter",
            frozenset({"agent_type", "capability"}),
        ),
        MetricDefinition("sentinelqa_agent_duplicate_work_ratio", "gauge", frozenset()),
        MetricDefinition("sentinelqa_agent_queue_depth", "gauge", frozenset({"agent_type"})),
        MetricDefinition(
            "sentinelqa_action_duration_seconds",
            "histogram",
            frozenset({"type", "outcome"}),
        ),
        MetricDefinition("sentinelqa_states_discovered_total", "counter", frozenset()),
        MetricDefinition(
            "sentinelqa_anomalies_total",
            "counter",
            frozenset({"oracle", "severity"}),
        ),
        MetricDefinition("sentinelqa_verification_reproduction_ratio", "gauge", frozenset()),
        MetricDefinition(
            "sentinelqa_llm_tokens_total",
            "counter",
            frozenset({"prompt", "model"}),
        ),
        MetricDefinition("sentinelqa_llm_cost_usd_total", "counter", frozenset()),
        MetricDefinition("sentinelqa_policy_denials_total", "counter", frozenset({"reason"})),
        MetricDefinition("sentinelqa_gate_decisions_total", "counter", frozenset({"outcome"})),
        MetricDefinition(
            "sentinelqa_artifact_integrity_failures_total",
            "counter",
            frozenset({"backend"}),
        ),
        MetricDefinition("sentinelqa_artifact_bytes", "histogram", frozenset({"kind", "backend"})),
    )
}


@dataclass(frozen=True, slots=True)
class MetricPoint:
    name: str
    kind: MetricKind
    value: float
    labels: tuple[tuple[str, str], ...]
    observed_at: datetime


class MetricsSink(Protocol):
    def increment(
        self, name: str, value: float = 1, labels: Mapping[str, str] | None = None
    ) -> None: ...

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None: ...

    def set(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None: ...


def _point(
    name: str,
    expected_kind: MetricKind,
    value: float,
    labels: Mapping[str, str] | None,
) -> MetricPoint:
    definition = METRICS.get(name)
    if definition is None:
        raise ValueError(f"unknown Qualestra metric: {name}")
    if definition.kind != expected_kind:
        raise ValueError(f"metric {name} is a {definition.kind}, not a {expected_kind}")
    values = labels or {}
    if set(values) != definition.labels:
        raise ValueError(
            f"metric {name} requires labels {sorted(definition.labels)}, got {sorted(values)}"
        )
    normalized: list[tuple[str, str]] = []
    for key, raw_value in sorted(values.items()):
        value_string = str(raw_value)
        if len(value_string) > 80:
            raise ValueError(f"metric label {key} exceeds 80 characters")
        normalized.append((key, value_string))
    return MetricPoint(name, expected_kind, float(value), tuple(normalized), datetime.now(UTC))


class NoopMetrics:
    """Zero-dependency production-safe default when no exporter is configured."""

    def increment(
        self, name: str, value: float = 1, labels: Mapping[str, str] | None = None
    ) -> None:
        _point(name, "counter", value, labels)

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        _point(name, "histogram", value, labels)

    def set(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        _point(name, "gauge", value, labels)


class InMemoryMetrics:
    """Thread-safe test/embedded sink following OpenTelemetry instrument semantics."""

    def __init__(self) -> None:
        self._points: list[MetricPoint] = []
        self._lock = threading.Lock()

    def increment(
        self, name: str, value: float = 1, labels: Mapping[str, str] | None = None
    ) -> None:
        self._append(_point(name, "counter", value, labels))

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        self._append(_point(name, "histogram", value, labels))

    def set(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        self._append(_point(name, "gauge", value, labels))

    def snapshot(self) -> tuple[MetricPoint, ...]:
        with self._lock:
            return tuple(self._points)

    def _append(self, point: MetricPoint) -> None:
        with self._lock:
            self._points.append(point)


class MetricsRecorder:
    """Domain-friendly, low-cardinality instrumentation facade."""

    def __init__(self, sink: MetricsSink | None = None) -> None:
        self.sink = sink or NoopMetrics()

    def run_completed(self, *, status: str, trigger: str) -> None:
        self.sink.increment("sentinelqa_runs_total", labels={"status": status, "trigger": trigger})

    def agent_task_completed(
        self,
        *,
        agent_type: str,
        outcome: str,
        duration_seconds: float,
        budget_used: BudgetUsed,
    ) -> None:
        labels = {"agent_type": agent_type, "outcome": outcome}
        self.sink.increment("sentinelqa_agent_tasks_total", labels=labels)
        self.sink.observe(
            "sentinelqa_agent_task_duration_seconds",
            duration_seconds,
            labels={"agent_type": agent_type},
        )
        for budget_kind, value in budget_used.model_dump().items():
            self.sink.observe(
                "sentinelqa_agent_budget_used",
                float(value),
                labels={"agent_type": agent_type, "budget_kind": budget_kind},
            )

    def queue_depth(self, *, agent_type: str, depth: int) -> None:
        self.sink.set("sentinelqa_agent_queue_depth", depth, labels={"agent_type": agent_type})

    def gate_decided(self, *, outcome: str) -> None:
        self.sink.increment("sentinelqa_gate_decisions_total", labels={"outcome": outcome})


def metrics_from_env(environ: Mapping[str, str] | None = None) -> MetricsSink:
    values = environ if environ is not None else os.environ
    mode = values.get("SENTINEL_TELEMETRY_MODE", "noop").lower()
    if mode == "noop":
        return NoopMetrics()
    if mode == "memory":
        return InMemoryMetrics()
    raise ValueError("SENTINEL_TELEMETRY_MODE must be 'noop' or 'memory'")
