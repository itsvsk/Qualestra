from uuid import uuid4

import pytest

from sentinelqa.gates.evaluator import GateEvaluator
from sentinelqa.verification.confidence import confidence_score
from sentinelqa.verification.minimizer import minimize_sequence


def test_confidence_threshold_formula():
    score = confidence_score(
        reproduction_rate=1,
        deterministic_signal_strength=1,
        invariant_strength=1,
        cross_layer_confirmation=1,
        clean_environment_confirmation=1,
        cross_browser_confirmation=1,
        environment_instability=0,
    )
    assert score == 1


@pytest.mark.asyncio
async def test_minimizer_preserves_failure_and_never_grows_sequence():
    original = [{"type": "login"}, {"type": "navigate"}, {"type": "fail"}]

    async def fails(steps):
        return any(step["type"] == "fail" for step in steps)

    minimized = await minimize_sequence(original, fails)
    assert minimized == [{"type": "fail"}]


def test_gate_is_deterministic_and_explains_operands():
    policy = {
        "block_when": [{"metric": "critical", "operator": ">", "value": 0}],
        "warn_when": [{"metric": "coverage", "operator": "<", "value": 0.75}],
    }
    metrics = {"critical": 1, "coverage": 1.0}
    evaluator = GateEvaluator()
    run_id = uuid4()
    first = evaluator.evaluate(run_id, metrics, policy)
    second = evaluator.evaluate(run_id, metrics, policy)
    assert first.model_dump(exclude={"generated_at"}) == second.model_dump(exclude={"generated_at"})
    assert str(first.outcome) == "BLOCK"
    assert first.rules[0].actual == 1
    assert first.rules[0].threshold == 0
