from __future__ import annotations

import asyncio
from copy import deepcopy
from uuid import UUID

from sentinelqa.domain.enums import TaskStatus
from sentinelqa.domain.models import AgentTask


class InMemoryTaskRepository:
    def __init__(self) -> None:
        self._tasks: dict[UUID, AgentTask] = {}
        self._status: dict[UUID, TaskStatus] = {}
        self._lock = asyncio.Lock()

    async def add(self, tasks: list[AgentTask]) -> None:
        async with self._lock:
            for task in tasks:
                existing = next(
                    (
                        item
                        for item in self._tasks.values()
                        if item.idempotency_key == task.idempotency_key
                    ),
                    None,
                )
                if existing and existing != task:
                    raise ValueError("idempotency key collision")
                self._tasks.setdefault(task.task_id, deepcopy(task))
                self._status.setdefault(task.task_id, TaskStatus(task.status))

    async def update_status(self, task_id: UUID, status: TaskStatus) -> None:
        async with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            self._status[task_id] = status

    async def for_run(self, run_id: UUID) -> list[tuple[AgentTask, TaskStatus]]:
        async with self._lock:
            return [
                (deepcopy(task), self._status[task.task_id])
                for task in self._tasks.values()
                if task.run_id == run_id
            ]
