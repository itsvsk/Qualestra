from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    AgentType,
    DeliveryMode,
    DeliveryStatus,
    GateOutcome,
    ReportFormat,
    ReportKind,
    RunStatus,
    SafetyClass,
    Severity,
    TaskStatus,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=True)


class Budget(FrozenModel):
    max_seconds: int = Field(default=300, gt=0)
    max_tool_calls: int = Field(default=40, ge=0)
    max_model_tokens: int = Field(default=0, ge=0)
    max_api_requests: int = Field(default=0, ge=0)
    max_artifact_bytes: int = Field(default=104_857_600, ge=0)


class BudgetUsed(BaseModel):
    seconds: float = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    model_tokens: int = Field(default=0, ge=0)
    api_requests: int = Field(default=0, ge=0)
    artifact_bytes: int = Field(default=0, ge=0)


class RunBudget(FrozenModel):
    max_minutes: int = Field(default=12, gt=0)
    max_actions: int = Field(default=80, ge=0)
    max_llm_cost_usd: float = Field(default=2, ge=0)
    max_api_requests: int = Field(default=500, ge=0)


class BrowserSettings(FrozenModel):
    viewport_width: int = Field(default=1440, ge=320, le=3840)
    viewport_height: int = Field(default=900, ge=320, le=2160)
    locale: str = Field(default="en-US", pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    timezone_id: str = "UTC"
    color_scheme: Literal["light", "dark", "no-preference"] = "light"
    reduced_motion: Literal["reduce", "no-preference"] = "no-preference"


class RunRequest(FrozenModel):
    target_id: str
    trigger: Literal["manual", "pull_request", "scheduled", "verification"] = "manual"
    commit_sha: str | None = None
    changed_files: tuple[str, ...] = ()
    changed_operations: tuple[str, ...] = ()
    coverage_gaps: tuple[str, ...] = ()
    personas: tuple[str, ...] = ("owner", "viewer")
    browsers: tuple[Literal["chromium", "firefox", "webkit"], ...] = ("chromium",)
    browser_settings: BrowserSettings = Field(default_factory=BrowserSettings)
    budget: RunBudget = Field(default_factory=RunBudget)
    policy_profile: str = "ephemeral_ci"
    seed: int = 41021

    @field_validator("browsers", "personas")
    @classmethod
    def selections_are_non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("at least one value is required")
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value


class Run(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    request: RunRequest
    status: RunStatus = RunStatus.QUEUED
    policy_version: str = "1"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    cancellation_requested: bool = False


class AgentManifest(FrozenModel):
    schema_version: str = "1.0"
    agent_type: AgentType
    version: str = "1.0"
    implementation: str
    capabilities: frozenset[str]
    input_schema: str
    result_schema: str
    budget: Budget
    concurrency_class: str
    max_attempts: int = Field(default=2, ge=1, le=5)


class AgentTask(FrozenModel):
    schema_version: str = "1.0"
    task_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    parent_task_id: UUID | None = None
    agent_type: AgentType
    agent_version: str = "1.0"
    status: TaskStatus = TaskStatus.READY
    dependency_task_ids: tuple[UUID, ...] = ()
    input_ref: str
    idempotency_key: str
    target_partition: str
    budget: Budget
    deadline: datetime
    attempt: int = Field(default=1, ge=1)

    @field_validator("idempotency_key")
    @classmethod
    def key_is_hashed(cls, value: str) -> str:
        if not value.startswith("sha256:"):
            raise ValueError("idempotency_key must start with sha256:")
        return value

    @staticmethod
    def stable_key(*parts: object) -> str:
        raw = "|".join(str(part) for part in parts)
        return f"sha256:{sha256(raw.encode()).hexdigest()}"


class AgentResult(FrozenModel):
    schema_version: str = "1.0"
    task_id: UUID
    agent_type: AgentType
    outcome: Literal["succeeded", "failed", "cancelled", "budget_exhausted"]
    result_ref: str
    summary: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    budget_used: BudgetUsed = Field(default_factory=BudgetUsed)
    produced_at: datetime = Field(default_factory=utc_now)


class PageInfo(FrozenModel):
    url: str
    route: str
    title: str
    landmarks: tuple[str, ...] = ()


class PersonaRef(FrozenModel):
    id: str
    role: Literal["owner", "admin", "editor", "viewer"]


class Control(FrozenModel):
    id: str
    type: str
    name: str
    enabled: bool = True
    visible: bool = True
    safety_hint: SafetyClass = SafetyClass.SAFE
    value: str | None = None


class SignalSet(FrozenModel):
    console_errors: tuple[str, ...] = ()
    failed_requests: tuple[dict[str, Any], ...] = ()
    responses: tuple[dict[str, Any], ...] = ()
    timings: tuple[dict[str, Any], ...] = ()
    accessibility_violations: tuple[dict[str, Any], ...] = ()


class EvidenceRefs(FrozenModel):
    screenshot_artifact_id: str | None = None
    trace_chunk_artifact_id: str | None = None
    dom_artifact_id: str | None = None


class Observation(FrozenModel):
    schema_version: str = "1.0"
    run_id: UUID
    session_id: UUID
    state_fingerprint: str
    page: PageInfo
    persona: PersonaRef
    controls: tuple[Control, ...] = ()
    signals: SignalSet = Field(default_factory=SignalSet)
    evidence: EvidenceRefs = Field(default_factory=EvidenceRefs)
    entities: tuple[dict[str, Any], ...] = ()
    environment: dict[str, Any] = Field(default_factory=dict)


class CandidateAction(FrozenModel):
    action_type: str
    control_id: str | None = None
    input: str | None = None
    safety_class: SafetyClass
    target_origin: str
    coverage_value: float = Field(default=0, ge=0, le=1)
    business_risk: float = Field(default=0, ge=0, le=1)
    change_relevance: float = Field(default=0, ge=0, le=1)
    historical_failure_probability: float = Field(default=0, ge=0, le=1)
    state_novelty: float = Field(default=0, ge=0, le=1)
    model_uncertainty: float = Field(default=0, ge=0, le=1)
    oracle_value: float = Field(default=0, ge=0, le=1)
    execution_cost: float = Field(default=0, ge=0, le=1)
    repetition_penalty: float = Field(default=0, ge=0, le=1)


class AuthorizedAction(FrozenModel):
    candidate: CandidateAction
    policy_version: str
    rationale_code: str
    authorized_at: datetime = Field(default_factory=utc_now)


class ActionRecord(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    action_type: str
    target_control_id: str | None = None
    inputs_redacted: dict[str, Any] = Field(default_factory=dict)
    safety_class: SafetyClass
    score: float
    rationale: str


class Transition(FrozenModel):
    from_state_id: str
    action_id: UUID
    to_state_id: str
    outcome: str
    duration_ms: int = Field(ge=0)


class Anomaly(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    oracle_type: str
    route: str
    action_type: str
    signal: str
    expected: str
    actual: str
    severity_hint: Severity
    evidence_refs: tuple[str, ...]
    suspected_component: str = "unknown"

    @property
    def signature(self) -> str:
        family = self.signal.strip().lower().replace(" ", "_")[:80]
        return "|".join(
            [self.oracle_type, self.route, self.action_type, family, self.suspected_component]
        )


class VerificationResult(FrozenModel):
    anomaly_id: UUID
    attempts: int = Field(ge=2)
    matched: int = Field(ge=0)
    minimized_steps: tuple[dict[str, Any], ...]
    confidence: float = Field(ge=0, le=1)
    verified: bool
    evidence_refs: tuple[str, ...]

    @model_validator(mode="after")
    def verified_requires_threshold(self) -> VerificationResult:
        if self.verified and self.confidence < 0.70:
            raise ValueError("verified defects require confidence >= 0.70")
        if self.matched > self.attempts:
            raise ValueError("matched cannot exceed attempts")
        return self


class Defect(FrozenModel):
    schema_version: str = "1.0"
    defect_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    title: str
    severity: Severity
    confidence: float = Field(ge=0.70, le=1)
    status: Literal["verified"] = "verified"
    affected_area: str
    signature: str
    persona: str
    preconditions: tuple[str, ...]
    steps_to_reproduce: tuple[dict[str, Any], ...]
    expected: str
    actual: str
    verification: dict[str, int]
    evidence: tuple[dict[str, Any], ...]
    suspected_component: str
    suggested_investigation: str
    created_at: datetime = Field(default_factory=utc_now)


class GateRuleResult(FrozenModel):
    metric: str
    operator: str
    threshold: Any
    actual: Any
    matched: bool
    level: Literal["block", "warn"]


class GateDecision(FrozenModel):
    schema_version: str = "1.0"
    run_id: UUID
    outcome: GateOutcome
    rules: tuple[GateRuleResult, ...]
    metrics: dict[str, Any]
    blockers: tuple[str, ...] = ()
    release_confidence: float = Field(ge=0, le=1)
    generated_at: datetime = Field(default_factory=utc_now)


def stable_digest(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"sha256:{sha256(raw.encode()).hexdigest()}"


class RunReport(FrozenModel):
    """Immutable, rendered report snapshot derived from committed run evidence."""

    schema_version: str = "1.0"
    report_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    kind: ReportKind
    format: ReportFormat
    content: str = Field(min_length=1)
    digest: str
    source_fingerprint: str
    generated_at: datetime

    @field_validator("digest", "source_fingerprint")
    @classmethod
    def fingerprint_is_sha256(cls, value: str) -> str:
        if len(value) != 71 or not value.startswith("sha256:"):
            raise ValueError("fingerprints must use sha256:<64 lowercase hexadecimal characters>")
        try:
            int(value.removeprefix("sha256:"), 16)
        except ValueError as error:
            raise ValueError(
                "fingerprints must contain lowercase hexadecimal characters"
            ) from error
        if value != value.lower():
            raise ValueError("fingerprints must contain lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def digest_matches_content(self) -> RunReport:
        if self.digest != stable_digest(self.content):
            raise ValueError("digest must match report content")
        return self


class ReleaseSummary(FrozenModel):
    """Deterministic release facts; generated prose may not alter these operands."""

    schema_version: str = "1.0"
    summary_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    report_id: UUID
    run_status: RunStatus
    gate_outcome: GateOutcome
    verified_defects: int = Field(ge=0)
    defects_by_severity: dict[str, int]
    coverage_metrics: dict[str, int | float]
    blockers: tuple[str, ...] = ()
    release_confidence: float = Field(ge=0, le=1)
    source_fingerprint: str
    generated_at: datetime

    @field_validator("source_fingerprint")
    @classmethod
    def source_is_sha256(cls, value: str) -> str:
        return RunReport.fingerprint_is_sha256(value)

    @field_validator("defects_by_severity")
    @classmethod
    def severity_counts_are_complete(cls, value: dict[str, int]) -> dict[str, int]:
        expected = {item.value for item in Severity}
        if set(value) != expected:
            raise ValueError(f"defects_by_severity must contain exactly {sorted(expected)}")
        if any(count < 0 for count in value.values()):
            raise ValueError("severity counts cannot be negative")
        return value

    @field_validator("coverage_metrics")
    @classmethod
    def coverage_values_are_non_negative(
        cls, value: dict[str, int | float]
    ) -> dict[str, int | float]:
        if any(metric < 0 for metric in value.values()):
            raise ValueError("coverage metrics cannot be negative")
        return value

    @model_validator(mode="after")
    def defect_total_matches_breakdown(self) -> ReleaseSummary:
        if self.verified_defects != sum(self.defects_by_severity.values()):
            raise ValueError("verified_defects must equal the severity-count total")
        return self


class DeliveryError(FrozenModel):
    """Bounded, pre-redacted error metadata safe to retain in an audit trail."""

    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    category: Literal["transient", "permanent", "policy", "configuration", "unknown"]
    retryable: bool
    redacted_message: str = Field(min_length=1, max_length=500)

    @field_validator("redacted_message")
    @classmethod
    def message_must_be_redacted(cls, value: str) -> str:
        lowered = value.lower()
        unsafe_markers = ("authorization:", "bearer ", "api_key=", "apikey=", "token=")
        if any(marker in lowered for marker in unsafe_markers):
            raise ValueError("error metadata contains credential-like material")
        return value


class DeliveryRequest(FrozenModel):
    """Provider-neutral request referencing only configured connector destinations."""

    schema_version: str = "1.0"
    delivery_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    report_id: UUID
    report_digest: str
    report_format: ReportFormat
    connector_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    destination_id: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    mode: DeliveryMode
    idempotency_key: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("report_digest", "idempotency_key")
    @classmethod
    def digest_is_sha256(cls, value: str) -> str:
        return RunReport.fingerprint_is_sha256(value)

    @staticmethod
    def stable_key(
        run_id: UUID,
        report_digest: str,
        connector_id: str,
        destination_id: str,
        mode: DeliveryMode | str,
    ) -> str:
        return stable_digest(run_id, report_digest, connector_id, destination_id, mode)

    @model_validator(mode="after")
    def idempotency_key_matches_request(self) -> DeliveryRequest:
        expected = self.stable_key(
            self.run_id,
            self.report_digest,
            self.connector_id,
            self.destination_id,
            self.mode,
        )
        if self.idempotency_key != expected:
            raise ValueError("idempotency_key must match the delivery request operands")
        return self


class DeliveryRecord(FrozenModel):
    schema_version: str = "1.0"
    request: DeliveryRequest
    status: DeliveryStatus
    attempt_count: int = Field(default=0, ge=0)
    last_error: DeliveryError | None = None
    approved_at: datetime | None = None
    next_attempt_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def state_is_consistent(self) -> DeliveryRecord:
        if (
            self.status == DeliveryStatus.PENDING_APPROVAL
            and self.request.mode != DeliveryMode.MANUAL
        ):
            raise ValueError("only manual deliveries may await approval")
        if self.status == DeliveryStatus.FAILED and self.last_error is None:
            raise ValueError("failed deliveries require last_error metadata")
        if self.status != DeliveryStatus.FAILED and self.last_error is not None:
            raise ValueError("last_error is only valid for failed deliveries")
        if self.approved_at is not None and self.request.mode != DeliveryMode.MANUAL:
            raise ValueError("approved_at is only valid for manual deliveries")
        approval_required_states = {
            DeliveryStatus.QUEUED,
            DeliveryStatus.PROCESSING,
            DeliveryStatus.SUCCEEDED,
            DeliveryStatus.FAILED,
        }
        if (
            self.request.mode == DeliveryMode.MANUAL
            and self.status in approval_required_states
            and self.approved_at is None
        ):
            raise ValueError("manual deliveries require approval before they can be queued")
        return self


class DeliveryResult(FrozenModel):
    schema_version: str = "1.0"
    delivery_id: UUID
    attempt: int = Field(ge=1)
    status: Literal["succeeded", "failed"]
    connector_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    external_id: str | None = Field(default=None, max_length=240)
    external_url: str | None = Field(default=None, max_length=2000)
    error: DeliveryError | None = None
    recorded_at: datetime = Field(default_factory=utc_now)

    @field_validator("external_url")
    @classmethod
    def external_url_is_a_safe_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "external_url must be an HTTP(S) URL without credentials or query data"
            )
        return value

    @model_validator(mode="after")
    def result_is_consistent(self) -> DeliveryResult:
        if self.status == "failed" and self.error is None:
            raise ValueError("failed delivery results require error metadata")
        if self.status == "succeeded" and self.error is not None:
            raise ValueError("successful delivery results cannot contain error metadata")
        return self
