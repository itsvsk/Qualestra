from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    EXPLORING = "exploring"
    ANALYZING = "analyzing"
    VERIFYING = "verifying"
    REPORTING = "reporting"
    GATING = "gating"
    PASSED = "passed"
    WARNED = "warned"
    BLOCKED = "blocked"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class AgentType(StrEnum):
    CHANGE_IMPACT = "change_impact"
    UI_EXPLORER = "ui_explorer"
    API_EXPLORER = "api_explorer"
    DATA_INVARIANT = "data_invariant"
    VERIFICATION = "verification"
    DEFECT_TRIAGE = "defect_triage"


class SafetyClass(StrEnum):
    SAFE = "safe"
    CONTROLLED = "controlled"
    RESTRICTED = "restricted"


class GateOutcome(StrEnum):
    PASS = "PASS"  # noqa: S105 - gate outcome, not a credential
    WARN = "WARN"
    BLOCK = "BLOCK"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FailureClass(StrEnum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    POLICY_DENIED = "policy_denied"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    INVALID_RESULT = "invalid_result"


class ReportKind(StrEnum):
    TECHNICAL = "technical"
    EXECUTIVE = "executive"
    GATE = "gate"
    COMPARISON = "comparison"
    DEFECT = "defect"


class ReportFormat(StrEnum):
    JSON = "json"
    MARKDOWN = "markdown"
    HTML = "html"


class DeliveryMode(StrEnum):
    DRY_RUN = "dry_run"
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class DeliveryStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
