from dataclasses import dataclass


@dataclass(slots=True)
class StopPolicy:
    max_actions: int = 100
    max_consecutive_no_novelty: int = 8
    max_action_repeats_per_state: int = 2

    def should_stop(self, actions: int, no_novelty: int) -> tuple[bool, str]:
        if actions >= self.max_actions:
            return True, "action_budget_exhausted"
        if no_novelty >= self.max_consecutive_no_novelty:
            return True, "novelty_window_exhausted"
        return False, "continue"
