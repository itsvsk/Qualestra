from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from sentinelqa.domain.enums import SafetyClass
from sentinelqa.domain.models import AuthorizedAction, CandidateAction, Observation


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    rationale_code: str


class PolicyEngine:
    def __init__(
        self,
        allowed_origins: set[str],
        allowed_classes: set[SafetyClass] | None = None,
        unattended: bool = True,
        kill_switch: Callable[[], bool] = lambda: False,
        version: str = "1",
    ) -> None:
        self.allowed_origins = {origin.rstrip("/") for origin in allowed_origins}
        self.allowed_classes = allowed_classes or {SafetyClass.SAFE, SafetyClass.CONTROLLED}
        self.unattended = unattended
        self.kill_switch = kill_switch
        self.version = version

    @staticmethod
    def origin(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def authorize(
        self,
        candidate: CandidateAction,
        observation: Observation | None = None,
        explicit_restricted_approval: bool = False,
    ) -> PolicyDecision:
        if self.kill_switch():
            return PolicyDecision(False, "kill_switch_active")
        if self.origin(candidate.target_origin) not in self.allowed_origins:
            return PolicyDecision(False, "origin_not_allowlisted")
        if candidate.control_id and observation:
            current_ids = {control.id for control in observation.controls}
            if candidate.control_id not in current_ids:
                return PolicyDecision(False, "stale_or_unknown_control")
        safety = SafetyClass(candidate.safety_class)
        if safety == SafetyClass.RESTRICTED:
            if self.unattended or not explicit_restricted_approval:
                return PolicyDecision(False, "restricted_action_denied")
        elif safety not in self.allowed_classes:
            return PolicyDecision(False, "safety_class_denied")
        return PolicyDecision(True, "policy_allowed")

    def require_authorized(
        self, candidate: CandidateAction, observation: Observation | None = None
    ) -> AuthorizedAction:
        decision = self.authorize(candidate, observation)
        if not decision.allowed:
            raise PermissionError(decision.rationale_code)
        return AuthorizedAction(
            candidate=candidate, policy_version=self.version, rationale_code=decision.rationale_code
        )
