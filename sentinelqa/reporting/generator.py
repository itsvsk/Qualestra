from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from hashlib import sha256
from typing import Any

from sentinelqa.domain.models import Defect, GateDecision, Run

from .contracts import (
    EvidenceReference,
    ExecutiveSummary,
    GateRuleSummary,
    MetricDelta,
    ReleaseDecisionSummary,
    ReportDefect,
    RunComparison,
    RunReportDocument,
    SeverityCounts,
    TechnicalSummary,
)
from .redaction import redact_report_text, redact_report_value

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def report_source_fingerprint(run: Run, defects: list[Defect], gate: GateDecision) -> str:
    """Fingerprint all committed report inputs without disclosing their content."""

    _validate_inputs(run, defects, gate)
    source = {
        "run": run.model_dump(mode="json"),
        "defects": [
            defect.model_dump(mode="json")
            for defect in sorted(defects, key=lambda item: str(item.defect_id))
        ],
        "gate": gate.model_dump(mode="json"),
    }
    return _stable_id("source", "1.0", _canonical(source))


def generate_run_report(run: Run, defects: list[Defect], gate: GateDecision) -> RunReportDocument:
    """Build a deterministic report exclusively from committed run outputs."""

    _validate_inputs(run, defects, gate)
    ordered_defects = tuple(
        sorted(
            defects,
            key=lambda defect: (
                SEVERITY_ORDER[str(defect.severity)],
                defect.signature,
                str(defect.defect_id),
            ),
        )
    )
    report_defects = tuple(_report_defect(defect) for defect in ordered_defects)
    severity_counts = _severity_counts(ordered_defects)
    evidence_references = tuple(
        sorted(
            (
                EvidenceReference(
                    defect_id=defect.defect_id,
                    kind=redact_report_text(str(item.get("kind", "artifact"))),
                    artifact_id=redact_report_text(str(item.get("artifact_id", ""))),
                )
                for defect in ordered_defects
                for item in defect.evidence
            ),
            key=lambda item: (str(item.defect_id), item.kind, item.artifact_id),
        )
    )
    technical = TechnicalSummary(
        run_id=run.id,
        target_id=redact_report_text(run.request.target_id),
        trigger=run.request.trigger,
        commit_sha=redact_report_text(run.request.commit_sha) if run.request.commit_sha else None,
        run_status=str(run.status),
        started_at=run.started_at,
        ended_at=run.ended_at,
        duration_seconds=_duration_seconds(run.started_at, run.ended_at),
        verified_defect_count=len(report_defects),
        severity_counts=severity_counts,
        affected_areas=tuple(sorted({defect.affected_area for defect in report_defects})),
        suspected_components=tuple(
            sorted({defect.suspected_component for defect in report_defects})
        ),
        personas=tuple(sorted({defect.persona for defect in report_defects})),
        evidence_reference_count=len(evidence_references),
    )
    release = _release_summary(gate)
    executive = _executive_summary(technical, release)
    generated_at = gate.generated_at
    report_id = _stable_id(
        "run",
        "1.0",
        "sentinelqa.run",
        _canonical(technical),
        _canonical(executive),
        _canonical(release),
        _canonical(report_defects),
        _canonical(evidence_references),
    )
    return RunReportDocument(
        report_id=report_id,
        generated_at=generated_at,
        technical_summary=technical,
        executive_summary=executive,
        release_summary=release,
        defects=report_defects,
        evidence_references=evidence_references,
    )


def compare_run_reports(baseline: RunReportDocument, current: RunReportDocument) -> RunComparison:
    """Compare committed reports without inference or generated prose."""

    baseline_signatures = {defect.signature for defect in baseline.defects}
    current_signatures = {defect.signature for defect in current.defects}
    numeric_keys = sorted(
        key
        for key in baseline.release_summary.metrics.keys() & current.release_summary.metrics.keys()
        if _is_number(baseline.release_summary.metrics[key])
        and _is_number(current.release_summary.metrics[key])
    )
    deltas = tuple(
        MetricDelta(
            metric=key,
            baseline=baseline.release_summary.metrics[key],
            current=current.release_summary.metrics[key],
            delta=current.release_summary.metrics[key] - baseline.release_summary.metrics[key],
        )
        for key in numeric_keys
    )
    defect_delta = (
        current.technical_summary.verified_defect_count
        - baseline.technical_summary.verified_defect_count
    )
    outcome_change = (
        f"Gate outcome changed from {baseline.release_summary.outcome} "
        f"to {current.release_summary.outcome}."
        if baseline.release_summary.outcome != current.release_summary.outcome
        else f"Gate outcome remained {current.release_summary.outcome}."
    )
    narrative = (
        f"{outcome_change} Verified defect count changed by {defect_delta:+d}: "
        f"{baseline.technical_summary.verified_defect_count} in the baseline and "
        f"{current.technical_summary.verified_defect_count} in the current run."
    )
    return RunComparison(
        comparison_id=_stable_id("comparison", baseline.report_id, current.report_id),
        baseline_run_id=baseline.technical_summary.run_id,
        current_run_id=current.technical_summary.run_id,
        baseline_outcome=baseline.release_summary.outcome,
        current_outcome=current.release_summary.outcome,
        verified_defect_delta=defect_delta,
        new_defect_signatures=tuple(sorted(current_signatures - baseline_signatures)),
        resolved_defect_signatures=tuple(sorted(baseline_signatures - current_signatures)),
        persistent_defect_signatures=tuple(sorted(baseline_signatures & current_signatures)),
        metric_deltas=deltas,
        narrative=narrative,
    )


def _validate_inputs(run: Run, defects: list[Defect], gate: GateDecision) -> None:
    if gate.run_id != run.id:
        raise ValueError("gate decision does not belong to the report run")
    mismatched = sorted(str(defect.defect_id) for defect in defects if defect.run_id != run.id)
    if mismatched:
        raise ValueError(f"defects do not belong to the report run: {', '.join(mismatched)}")
    signatures = [defect.signature for defect in defects]
    if len(signatures) != len(set(signatures)):
        raise ValueError("a run report may contain only one verified defect per signature")
    matched_blockers = tuple(
        sorted(rule.metric for rule in gate.rules if rule.level == "block" and rule.matched)
    )
    matched_warnings = tuple(
        sorted(rule.metric for rule in gate.rules if rule.level == "warn" and rule.matched)
    )
    expected_outcome = "BLOCK" if matched_blockers else "WARN" if matched_warnings else "PASS"
    if tuple(sorted(gate.blockers)) != matched_blockers:
        raise ValueError("gate blockers do not match the committed rule operands")
    if str(gate.outcome) != expected_outcome:
        raise ValueError("gate outcome does not match the committed rule operands")


def _severity_counts(defects: tuple[Defect, ...]) -> SeverityCounts:
    counts = Counter(str(defect.severity) for defect in defects)
    return SeverityCounts(
        critical=counts["critical"],
        high=counts["high"],
        medium=counts["medium"],
        low=counts["low"],
    )


def _report_defect(defect: Defect) -> ReportDefect:
    return ReportDefect(
        defect_id=defect.defect_id,
        title=redact_report_text(defect.title),
        severity=str(defect.severity),
        confidence=defect.confidence,
        affected_area=redact_report_text(defect.affected_area),
        signature=redact_report_text(defect.signature),
        persona=redact_report_text(defect.persona),
        suspected_component=redact_report_text(defect.suspected_component),
        preconditions=tuple(redact_report_text(item) for item in defect.preconditions),
        steps_to_reproduce=tuple(redact_report_value(step) for step in defect.steps_to_reproduce),
        expected=redact_report_text(defect.expected),
        actual=redact_report_text(defect.actual),
        verification=dict(sorted(defect.verification.items())),
        evidence=tuple(redact_report_value(item) for item in defect.evidence),
        suggested_investigation=redact_report_text(defect.suggested_investigation),
        created_at=defect.created_at,
    )


def _release_summary(gate: GateDecision) -> ReleaseDecisionSummary:
    rules = tuple(
        GateRuleSummary(
            metric=redact_report_text(rule.metric),
            operator=rule.operator,
            threshold=redact_report_value(rule.threshold),
            actual=redact_report_value(rule.actual),
            matched=rule.matched,
            level=rule.level,
        )
        for rule in sorted(
            gate.rules,
            key=lambda item: (item.level, item.metric, item.operator, repr(item.threshold)),
        )
    )
    blockers = tuple(sorted({redact_report_text(item) for item in gate.blockers}))
    warnings = tuple(
        sorted({rule.metric for rule in rules if rule.level == "warn" and rule.matched})
    )
    outcome = str(gate.outcome)
    if outcome == "BLOCK":
        narrative = (
            f"Release gate BLOCKED with {len(blockers)} blocking rule"
            f"{'' if len(blockers) == 1 else 's'} matched."
        )
    elif outcome == "WARN":
        narrative = (
            f"Release gate completed with {len(warnings)} warning rule"
            f"{'' if len(warnings) == 1 else 's'} matched."
        )
    else:
        narrative = "Release gate PASSED with no blocking or warning rules matched."
    return ReleaseDecisionSummary(
        run_id=gate.run_id,
        outcome=outcome,
        release_confidence=gate.release_confidence,
        blockers=blockers,
        matched_warnings=warnings,
        metrics={
            redact_report_text(str(key)): redact_report_value(value)
            for key, value in sorted(gate.metrics.items())
        },
        rules=rules,
        generated_at=gate.generated_at,
        narrative=narrative,
    )


def _executive_summary(
    technical: TechnicalSummary, release: ReleaseDecisionSummary
) -> ExecutiveSummary:
    counts = technical.severity_counts
    headline = (
        f"{technical.target_id}: {release.outcome} "
        f"with {technical.verified_defect_count} verified defect"
        f"{'' if technical.verified_defect_count == 1 else 's'}"
    )
    narrative = (
        f"The {technical.trigger} run for {technical.target_id} has status "
        f"{technical.run_status}. "
        f"The deterministic release gate returned {release.outcome}. "
        f"The run recorded {counts.critical} critical, {counts.high} high, "
        f"{counts.medium} medium, and {counts.low} low verified defects."
    )
    findings = (
        f"Verified defects: {technical.verified_defect_count}.",
        f"Evidence references: {technical.evidence_reference_count}.",
        f"Release confidence: {release.release_confidence:.2f}.",
    )
    return ExecutiveSummary(headline=headline, narrative=narrative, key_findings=findings)


def _duration_seconds(started_at: datetime | None, ended_at: datetime | None) -> float | None:
    if started_at is None or ended_at is None:
        return None
    duration = (ended_at - started_at).total_seconds()
    if duration < 0:
        raise ValueError("run ended_at cannot precede started_at")
    return round(duration, 3)


def _stable_id(kind: str, *parts: str) -> str:
    digest = sha256("|".join((kind, *parts)).encode()).hexdigest()
    return f"sha256:{digest}"


def _canonical(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, tuple):
        value = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value
        ]
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
