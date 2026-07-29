from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from sentinelqa.domain.events import EventEnvelope
from sentinelqa.domain.models import AgentResult


class RemoteBlackboard:
    """Worker-side port for the control API's authoritative blackboard."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self._task_id: UUID | None = None

    def select_task(self, task_id: UUID) -> None:
        self._task_id = task_id

    async def get_context(self, ref: str) -> dict[str, Any]:
        del ref
        if self._task_id is None:
            raise RuntimeError("no task selected")
        response = await self.client.get(f"/internal/worker/tasks/{self._task_id}/context")
        response.raise_for_status()
        return dict(response.json())

    async def commit_result(self, result: AgentResult, event: EventEnvelope) -> None:
        response = await self.client.post(
            f"/internal/worker/tasks/{result.task_id}/result",
            json={
                "result": result.model_dump(mode="json"),
                "event": event.model_dump(mode="json"),
            },
        )
        response.raise_for_status()
