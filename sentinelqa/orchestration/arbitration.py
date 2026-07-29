from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ArbitrationDecision:
    subject_signature: str
    inputs: tuple[dict[str, Any], ...]
    precedence_rule: str
    outcome: str
    rationale_code: str


def arbitrate(subject_signature: str, inputs: list[dict[str, Any]]) -> ArbitrationDecision:
    deterministic = [item for item in inputs if item.get("evidence_kind") == "deterministic"]
    values = {str(item.get("outcome")) for item in deterministic}
    if len(values) > 1:
        return ArbitrationDecision(
            subject_signature,
            tuple(inputs),
            "preserve_deterministic_contradiction",
            "verification_required",
            "contradictory_deterministic_evidence",
        )
    if deterministic:
        return ArbitrationDecision(
            subject_signature,
            tuple(inputs),
            "deterministic_over_semantic",
            str(deterministic[0]["outcome"]),
            "deterministic_evidence_precedence",
        )
    return ArbitrationDecision(
        subject_signature,
        tuple(inputs),
        "semantic_requires_review",
        "review_observation",
        "no_deterministic_confirmation",
    )
