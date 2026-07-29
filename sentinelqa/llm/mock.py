from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from .client import LLMUnavailable

OutputT = TypeVar("OutputT", bound=BaseModel)


class MockLLMClient:
    provider = "mock"

    def __init__(
        self, fixtures: dict[str, dict[str, Any]] | None = None, unavailable: bool = False
    ) -> None:
        self.fixtures = fixtures or {}
        self.unavailable = unavailable
        self.calls: list[tuple[str, str]] = []

    async def structured(
        self,
        *,
        prompt_name: str,
        prompt_version: str,
        system: str,
        user: str,
        output_type: type[OutputT],
    ) -> OutputT:
        del system, user
        self.calls.append((prompt_name, prompt_version))
        if self.unavailable:
            raise LLMUnavailable("simulated model outage")
        fixture = self.fixtures.get(prompt_name)
        if fixture is None:
            fixture = self._default(prompt_name)
        return output_type.model_validate(fixture)

    @staticmethod
    def _default(prompt_name: str) -> dict[str, Any]:
        if prompt_name == "planner":
            return {"hypotheses": [], "ranked_actions": []}
        if prompt_name == "change-impact":
            return {
                "schema_version": "1.0",
                "impacts": [],
                "task_slices": [],
                "budget_allocation": {"direct": 0.6, "adjacent": 0.25, "smoke": 0.15},
                "uncertainties": ["no model fixture; deterministic baseline applied"],
            }
        return {}
