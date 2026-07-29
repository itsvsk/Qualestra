from urllib.parse import urlsplit

from sentinelqa.domain.models import CandidateAction, Observation
from sentinelqa.policy.actions import classify_action


def candidates_from_observation(observation: Observation) -> list[CandidateAction]:
    parsed = urlsplit(observation.page.url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    output: list[CandidateAction] = []
    for control in observation.controls:
        if not (control.visible and control.enabled):
            continue
        action_type = "fill" if control.type in {"textbox", "input"} else "click"
        output.append(
            CandidateAction(
                action_type=action_type,
                control_id=control.id,
                safety_class=classify_action(action_type, control.name),
                target_origin=origin,
                coverage_value=1,
                business_risk=0.8 if "delete" in control.name.lower() else 0.4,
                state_novelty=1,
                oracle_value=0.5,
                execution_cost=0.2,
            )
        )
    return output
