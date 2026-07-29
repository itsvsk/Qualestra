from __future__ import annotations

import json
import logging

import pytest

from sentinelqa.domain.models import BudgetUsed
from sentinelqa.observability import (
    InMemoryMetrics,
    InMemoryTracer,
    MetricsRecorder,
    StructuredLogger,
    metrics_from_env,
    tracer_from_env,
)


def test_metrics_recorder_emits_low_cardinality_run_and_agent_metrics() -> None:
    sink = InMemoryMetrics()
    recorder = MetricsRecorder(sink)

    recorder.run_completed(status="passed", trigger="pull_request")
    recorder.agent_task_completed(
        agent_type="ui_explorer",
        outcome="succeeded",
        duration_seconds=1.25,
        budget_used=BudgetUsed(tool_calls=3, artifact_bytes=120),
    )
    recorder.queue_depth(agent_type="ui_explorer", depth=2)
    recorder.gate_decided(outcome="PASS")

    points = sink.snapshot()
    assert {point.name for point in points} >= {
        "sentinelqa_runs_total",
        "sentinelqa_agent_tasks_total",
        "sentinelqa_agent_task_duration_seconds",
        "sentinelqa_agent_budget_used",
        "sentinelqa_agent_queue_depth",
        "sentinelqa_gate_decisions_total",
    }
    assert not any("run_id" in dict(point.labels) for point in points)


def test_metrics_reject_unknown_or_high_cardinality_labels() -> None:
    sink = InMemoryMetrics()
    with pytest.raises(ValueError, match="requires labels"):
        sink.increment(
            "sentinelqa_runs_total",
            labels={"status": "passed", "trigger": "manual", "run_id": "not-allowed"},
        )
    with pytest.raises(ValueError, match="unknown"):
        sink.increment("sentinelqa_unregistered_total")


def test_telemetry_mode_is_explicit_and_offline_safe() -> None:
    assert isinstance(metrics_from_env({"SENTINEL_TELEMETRY_MODE": "memory"}), InMemoryMetrics)
    assert isinstance(tracer_from_env({"SENTINEL_TELEMETRY_MODE": "memory"}), InMemoryTracer)
    with pytest.raises(ValueError, match="SENTINEL_TELEMETRY_MODE"):
        metrics_from_env({"SENTINEL_TELEMETRY_MODE": "remote"})


def test_in_memory_tracer_preserves_parentage_and_error_status() -> None:
    tracer = InMemoryTracer()
    with tracer.start_as_current_span("run", {"run.trigger": "manual"}) as run_span:
        run_span.set_attribute("run.status", "exploring")
        with pytest.raises(RuntimeError):
            with tracer.start_as_current_span("agent.execute", {"agent.type": "api_explorer"}):
                raise RuntimeError("synthetic failure")

    child, parent = tracer.finished_spans()
    assert child.parent_span_id == parent.span_id
    assert child.trace_id == parent.trace_id
    assert child.status == "error"
    assert parent.status == "ok"
    with pytest.raises(ValueError, match="secret"):
        tracer.start_as_current_span("unsafe", {"api.token": "do-not-record"})


def test_structured_logger_redacts_and_correlates(caplog) -> None:
    logger = logging.getLogger("sentinelqa-test")
    structured = StructuredLogger(logger)

    with caplog.at_level(logging.INFO, logger="sentinelqa-test"):
        structured.event(
            "agent.completed",
            fields={"Authorization": "Bearer abcdefghijklmnop", "outcome": "succeeded"},
        )

    payload = json.loads(caplog.records[-1].message)
    assert payload["event_type"] == "agent.completed"
    assert payload["fields"] == {"Authorization": "[REDACTED]", "outcome": "succeeded"}
