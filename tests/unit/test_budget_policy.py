import asyncio

import pytest

from sentinelqa.domain.enums import SafetyClass
from sentinelqa.domain.models import Budget, CandidateAction
from sentinelqa.policy.budgets import AtomicBudget, BudgetExhausted
from sentinelqa.policy.engine import PolicyEngine


@pytest.mark.asyncio
async def test_atomic_budget_prevents_concurrent_overspend():
    budget = AtomicBudget(Budget(max_tool_calls=1))
    results = await asyncio.gather(
        budget.consume("tool_calls"), budget.consume("tool_calls"), return_exceptions=True
    )
    assert sum(isinstance(item, BudgetExhausted) for item in results) == 1
    assert budget.used.tool_calls == 1


def test_budget_subdivision_is_exact_and_deterministic():
    assert AtomicBudget.subdivide(10, [0.6, 0.25, 0.15]) == [6, 3, 1]


def candidate(safety=SafetyClass.SAFE, origin="http://atlas-web:5173"):
    return CandidateAction(
        action_type="click",
        control_id="control_1",
        safety_class=safety,
        target_origin=origin,
    )


def test_policy_denies_restricted_and_foreign_origin():
    policy = PolicyEngine({"http://atlas-web:5173"})
    assert policy.authorize(candidate(SafetyClass.RESTRICTED)).rationale_code == (
        "restricted_action_denied"
    )
    assert policy.authorize(candidate(origin="https://attacker.example")).rationale_code == (
        "origin_not_allowlisted"
    )


def test_kill_switch_denies_every_action():
    policy = PolicyEngine({"http://atlas-web:5173"}, kill_switch=lambda: True)
    assert policy.authorize(candidate()).rationale_code == "kill_switch_active"
