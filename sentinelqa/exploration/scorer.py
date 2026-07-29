from __future__ import annotations

from dataclasses import dataclass

from sentinelqa.domain.models import CandidateAction


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    coverage_value: float = 0.23
    business_risk: float = 0.21
    change_relevance: float = 0.18
    historical_failure_probability: float = 0.13
    state_novelty: float = 0.10
    model_uncertainty: float = 0.08
    oracle_value: float = 0.07
    execution_cost: float = -0.10
    repetition_penalty: float = -0.15
    policy_violation: float = -1.00


DEFAULT_WEIGHTS = ScoreWeights()


def score_action(
    candidate: CandidateAction,
    policy_violation: bool = False,
    weights: ScoreWeights = DEFAULT_WEIGHTS,
) -> float:
    if policy_violation:
        return float("-inf")
    return float(
        sum(
            getattr(candidate, field) * getattr(weights, field)
            for field in (
                "coverage_value",
                "business_risk",
                "change_relevance",
                "historical_failure_probability",
                "state_novelty",
                "model_uncertainty",
                "oracle_value",
                "execution_cost",
                "repetition_penalty",
            )
        )
    )


def rank_actions(candidates: list[CandidateAction]) -> list[CandidateAction]:
    return sorted(
        candidates,
        key=lambda item: (
            -score_action(item),
            item.action_type,
            item.control_id or "",
            item.input or "",
        ),
    )
