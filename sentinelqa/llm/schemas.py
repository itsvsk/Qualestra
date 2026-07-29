from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str
    risk: Literal["low", "medium", "high", "critical"]
    oracle: str


class RankedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    control_id: str
    action_type: str
    input: str | None = None
    expected_observation: str
    reason: str
    model_priority: float = Field(ge=0, le=1)


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypotheses: list[Hypothesis]
    ranked_actions: list[RankedAction]


class Impact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature: str
    classification: Literal["direct", "adjacent", "unknown"]
    risk_score: float = Field(ge=0, le=1)
    evidence: list[str]


class TestCharter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    impacts: list[Impact]
    task_slices: list[dict[str, object]]
    budget_allocation: dict[str, float]
    uncertainties: list[str]
