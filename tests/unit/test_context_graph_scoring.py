from sentinelqa.blackboard.context_projection import project_context
from sentinelqa.domain.models import CandidateAction, Control
from sentinelqa.exploration.observer import state_fingerprint
from sentinelqa.exploration.scorer import rank_actions, score_action


def test_context_projection_does_not_leak_peer_hidden_data():
    source = {"charter": {}, "persona": "viewer", "hidden_prompt_history": "secret"}
    assert project_context("ui_explorer", source) == {"charter": {}, "persona": "viewer"}


def test_state_fingerprint_ignores_volatile_values():
    first = Control(id="a", type="button", name="Save", value="timestamp:1")
    second = Control(id="b", type="button", name="Save", value="timestamp:2")
    assert state_fingerprint("/projects/123", ["main"], [first], "editor") == state_fingerprint(
        "/projects/456", ["main"], [second], "editor"
    )


def test_scoring_is_deterministic_and_policy_violation_loses():
    low = CandidateAction(
        action_type="click",
        control_id="b",
        safety_class="safe",
        target_origin="http://atlas-web:5173",
        coverage_value=0.1,
    )
    high = low.model_copy(update={"control_id": "a", "coverage_value": 1.0})
    assert rank_actions([low, high]) == [high, low]
    assert score_action(high, policy_violation=True) == float("-inf")
