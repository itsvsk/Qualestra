from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sentinelqa.domain.models import ActionRecord, Observation, Transition


@dataclass(slots=True)
class StateNode:
    fingerprint: str
    route: str
    title: str
    role: str
    first_seen_at: datetime
    visited_count: int = 1
    anomalies: set[str] = field(default_factory=set)


class ProductGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, StateNode] = {}
        self.transitions: dict[tuple[str, str, str], Transition] = {}

    def observe(self, observation: Observation) -> StateNode:
        node = self.nodes.get(observation.state_fingerprint)
        if node:
            node.visited_count += 1
            return node
        node = StateNode(
            fingerprint=observation.state_fingerprint,
            route=observation.page.route,
            title=observation.page.title,
            role=observation.persona.role,
            first_seen_at=datetime.now(UTC),
        )
        self.nodes[node.fingerprint] = node
        return node

    def connect(
        self,
        before: Observation,
        action: ActionRecord,
        after: Observation,
        outcome: str,
        duration_ms: int,
    ) -> Transition:
        self.observe(before)
        self.observe(after)
        transition = Transition(
            from_state_id=before.state_fingerprint,
            action_id=action.id,
            to_state_id=after.state_fingerprint,
            outcome=outcome,
            duration_ms=duration_ms,
        )
        key = (before.state_fingerprint, str(action.id), after.state_fingerprint)
        self.transitions.setdefault(key, transition)
        return self.transitions[key]

    def coverage(self) -> dict[str, int]:
        return {
            "states": len(self.nodes),
            "transitions": len(self.transitions),
            "routes": len({node.route for node in self.nodes.values()}),
            "roles": len({node.role for node in self.nodes.values()}),
        }
