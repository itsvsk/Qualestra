from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sentinelqa.domain.models import AgentResult, AgentTask


class PartitionScheduler:
    """Runs independent partitions concurrently and serializes matching partitions."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def _lock_for(self, partition: str) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(partition, asyncio.Lock())

    async def run(
        self,
        tasks: list[AgentTask],
        execute: Callable[[AgentTask], Awaitable[AgentResult]],
    ) -> list[AgentResult]:
        results: list[AgentResult] = []

        async def one(task: AgentTask) -> None:
            lock = await self._lock_for(task.target_partition)
            async with lock:
                results.append(await execute(task))

        async with asyncio.TaskGroup() as group:
            for task in tasks:
                group.create_task(one(task), name=f"agent:{task.agent_type}:{task.task_id}")
        return sorted(results, key=lambda result: str(result.task_id))
