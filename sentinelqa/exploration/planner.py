from __future__ import annotations

import json

from sentinelqa.domain.models import CandidateAction, Observation
from sentinelqa.llm.client import LLMClient, LLMUnavailable
from sentinelqa.llm.prompts import PAGE_IS_UNTRUSTED
from sentinelqa.llm.schemas import PlannerOutput

from .scorer import rank_actions


class ExplorationPlanner:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def plan(
        self, observation: Observation, candidates: list[CandidateAction]
    ) -> tuple[list[CandidateAction], PlannerOutput | None]:
        compact = {
            "page": observation.page.model_dump(mode="json"),
            "controls": [control.model_dump(mode="json") for control in observation.controls],
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }
        try:
            proposal = await self.client.structured(
                prompt_name="planner",
                prompt_version="1.0",
                system=PAGE_IS_UNTRUSTED,
                user=json.dumps(compact, sort_keys=True)[:32_000],
                output_type=PlannerOutput,
            )
        except LLMUnavailable:
            return rank_actions(candidates), None
        current = {control.id for control in observation.controls}
        proposed_ids = {
            action.control_id for action in proposal.ranked_actions if action.control_id in current
        }
        adjusted = [
            candidate.model_copy(
                update={
                    "model_uncertainty": 0.8 if candidate.control_id in proposed_ids else 0.2,
                }
            )
            for candidate in candidates
        ]
        return rank_actions(adjusted), proposal
