from datetime import UTC, datetime, timedelta

import pytest

from sentinelqa.domain.enums import AgentType
from sentinelqa.orchestration.task_graph import TaskGraph, TaskGraphError
from sentinelqa.storage.leases import InMemoryLeaseRepository


def test_task_graph_resolves_dependencies(task_factory):
    first = task_factory()
    second = task_factory(
        AgentType.UI_EXPLORER,
        dependency_task_ids=(first.task_id,),
        idempotency_key=task_factory().stable_key("second"),
    )
    graph = TaskGraph([first, second])
    assert graph.ready(set(), set()) == [first]
    assert graph.ready({first.task_id}, set()) == [second]


def test_task_graph_rejects_cycle(task_factory):
    first = task_factory()
    second = task_factory(AgentType.UI_EXPLORER, idempotency_key=task_factory().stable_key("2"))
    first = first.model_copy(update={"dependency_task_ids": (second.task_id,)})
    second = second.model_copy(update={"dependency_task_ids": (first.task_id,)})
    with pytest.raises(TaskGraphError, match="cycle"):
        TaskGraph([first, second])


@pytest.mark.asyncio
async def test_expired_lease_is_recoverable(task_factory):
    repo = InMemoryLeaseRepository(lease_seconds=45)
    task = task_factory()
    now = datetime.now(UTC)
    assert await repo.acquire(task.task_id, "worker-a", now)
    assert not await repo.acquire(task.task_id, "worker-b", now + timedelta(seconds=44))
    assert task.task_id in await repo.recoverable(now + timedelta(seconds=46))
    assert await repo.acquire(task.task_id, "worker-b", now + timedelta(seconds=46))
