from sentinelqa.exploration.memory import (
    ExplorationMemory,
    FailureClass,
    classify_attempts,
)


def test_history_prioritizes_gaps_and_verified_defects_deterministically() -> None:
    memory = ExplorationMemory()
    memory.restore(
        [
            {
                "feature": "billing",
                "verified_defects": 2,
                "failed_attempts": 3,
                "successful_attempts": 7,
                "coverage_gap": True,
            }
        ]
    )
    assert memory.risk_history() == {"billing": 0.825}


def test_failure_classification_separates_product_environment_and_tool_failures() -> None:
    assert classify_attempts([{"failed": True, "tool_error": True}]) == FailureClass.TOOL_FAILURE
    assert (
        classify_attempts([{"failed": True, "environment_error": True}])
        == FailureClass.ENVIRONMENT_FLAKE
    )
    assert (
        classify_attempts(
            [
                {"failed": True, "same_signature": True},
                {"failed": True, "same_signature": True},
            ]
        )
        == FailureClass.PRODUCT_DEFECT
    )
    assert classify_attempts([{"failed": True}]) == FailureClass.NON_REPRODUCIBLE
