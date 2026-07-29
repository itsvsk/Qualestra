from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

FAULTS = frozenset(f"F{index:02d}" for index in range(1, 11))


def seed_data() -> dict[str, Any]:
    return {
        "organizations": {
            "org-a": {"id": "org-a", "name": "Acme Test", "plan": "team", "project_limit": 3},
            "org-b": {"id": "org-b", "name": "Beacon Test", "plan": "free", "project_limit": 1},
        },
        "users": {
            "owner": {"id": "owner", "name": "Olivia Owner", "role": "owner", "org_id": "org-a"},
            "admin": {"id": "admin", "name": "Arun Admin", "role": "admin", "org_id": "org-a"},
            "editor": {"id": "editor", "name": "Elena Editor", "role": "editor", "org_id": "org-a"},
            "viewer": {
                "id": "viewer",
                "name": "Victor Viewer",
                "role": "viewer",
                "org_id": "org-a",
            },
            "new_user": {
                "id": "new_user",
                "name": "Nora New User",
                "role": "editor",
                "org_id": "org-a",
            },
            "other_owner": {
                "id": "other_owner",
                "name": "Bea Owner",
                "role": "owner",
                "org_id": "org-b",
            },
        },
        "projects": {
            "project-a": {"id": "project-a", "org_id": "org-a", "name": "Phoenix"},
            "project-b": {"id": "project-b", "org_id": "org-b", "name": "Beacon"},
        },
        "work_items": {
            "item-a": {
                "id": "item-a",
                "org_id": "org-a",
                "project_id": "project-a",
                "title": "Prepare launch",
                "status": "open",
            },
            "item-b": {
                "id": "item-b",
                "org_id": "org-b",
                "project_id": "project-b",
                "title": "Private roadmap",
                "status": "open",
            },
        },
        "exports": {},
        "audit_events": [],
        "webhook_deliveries": [],
        "sequence": {"work_item": 2, "export": 0, "audit": 0},
    }


@dataclass
class AtlasState:
    data: dict[str, Any] = field(default_factory=seed_data)
    faults: set[str] = field(default_factory=set)
    seed_number: int = 41021

    def reset(self) -> None:
        self.data = {}

    def seed(self, seed: int = 41021) -> None:
        self.seed_number = seed
        self.data = seed_data()

    def enable_profile(self, profile: str) -> None:
        if profile == "all":
            self.faults = set(FAULTS)
        elif profile == "fixed" or not profile:
            self.faults = set()
        else:
            requested = {part.strip().upper() for part in profile.split(",") if part.strip()}
            self.faults = requested & FAULTS

    def snapshot(self) -> dict[str, Any]:
        stable = deepcopy(self.data)
        stable["faults"] = sorted(self.faults)
        stable["seed"] = self.seed_number
        return stable

    def next_id(self, kind: str) -> str:
        self.data["sequence"][kind] += 1
        return f"{kind}-{self.data['sequence'][kind]}"

    def audit(self, org_id: str, actor_id: str, action: str, entity_id: str) -> None:
        event_id = self.next_id("audit")
        self.data["audit_events"].append(
            {
                "id": event_id,
                "org_id": org_id,
                "actor_id": actor_id,
                "action": action,
                "entity_id": entity_id,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
