from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReportModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=True)


class SeverityCounts(ReportModel):
    critical: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    low: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.critical + self.high + self.medium + self.low


class EvidenceReference(ReportModel):
    defect_id: UUID
    kind: str
    artifact_id: str


class ReportDefect(ReportModel):
    defect_id: UUID
    title: str
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0.70, le=1)
    status: Literal["verified"] = "verified"
    affected_area: str
    signature: str
    persona: str
    suspected_component: str
    preconditions: tuple[str, ...]
    steps_to_reproduce: tuple[dict[str, Any], ...]
    expected: str
    actual: str
    verification: dict[str, int]
    evidence: tuple[dict[str, Any], ...]
    suggested_investigation: str
    created_at: datetime


class TechnicalSummary(ReportModel):
    run_id: UUID
    target_id: str
    trigger: str
    commit_sha: str | None
    run_status: str
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None = Field(default=None, ge=0)
    verified_defect_count: int = Field(ge=0)
    severity_counts: SeverityCounts
    affected_areas: tuple[str, ...]
    suspected_components: tuple[str, ...]
    personas: tuple[str, ...]
    evidence_reference_count: int = Field(ge=0)


class ExecutiveSummary(ReportModel):
    headline: str
    narrative: str
    key_findings: tuple[str, ...]


class GateRuleSummary(ReportModel):
    metric: str
    operator: str
    threshold: Any
    actual: Any
    matched: bool
    level: Literal["block", "warn"]


class ReleaseDecisionSummary(ReportModel):
    run_id: UUID
    outcome: Literal["PASS", "WARN", "BLOCK"]
    release_confidence: float = Field(ge=0, le=1)
    blockers: tuple[str, ...]
    matched_warnings: tuple[str, ...]
    metrics: dict[str, Any]
    rules: tuple[GateRuleSummary, ...]
    generated_at: datetime
    narrative: str


class RunReportDocument(ReportModel):
    schema_version: Literal["1.0"] = "1.0"
    report_type: Literal["sentinelqa.run"] = "sentinelqa.run"
    report_id: str
    generated_at: datetime
    technical_summary: TechnicalSummary
    executive_summary: ExecutiveSummary
    release_summary: ReleaseDecisionSummary
    defects: tuple[ReportDefect, ...]
    evidence_references: tuple[EvidenceReference, ...]


class MetricDelta(ReportModel):
    metric: str
    baseline: int | float
    current: int | float
    delta: int | float


class RunComparison(ReportModel):
    schema_version: Literal["1.0"] = "1.0"
    report_type: Literal["sentinelqa.run-comparison"] = "sentinelqa.run-comparison"
    comparison_id: str
    baseline_run_id: UUID
    current_run_id: UUID
    baseline_outcome: Literal["PASS", "WARN", "BLOCK"]
    current_outcome: Literal["PASS", "WARN", "BLOCK"]
    verified_defect_delta: int
    new_defect_signatures: tuple[str, ...]
    resolved_defect_signatures: tuple[str, ...]
    persistent_defect_signatures: tuple[str, ...]
    metric_deltas: tuple[MetricDelta, ...]
    narrative: str
