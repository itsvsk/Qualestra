from __future__ import annotations

import asyncio

from apps.control_api.service import RunService
from sentinelqa.blackboard.memory import InMemoryBlackboard
from sentinelqa.domain.events import EventEnvelope
from sentinelqa.domain.models import AgentResult, AgentTask, RunRequest
from sentinelqa.orchestration.agent_registry import AgentRegistry
from sentinelqa.orchestration.supervisor import DeterministicSupervisor


async def test_worker_lease_commit_fanout_reaches_terminal_gate() -> None:
    blackboard = InMemoryBlackboard()
    registry = AgentRegistry.from_file("config/agents.yaml")
    service = RunService(DeterministicSupervisor(registry, blackboard))
    run = await service.create(RunRequest(target_id="atlas-local"))

    decision = None
    while task := await service.lease_task("test-worker"):
        await service.context_for_task(task.task_id)
        summary = (
            {"charter": {"impacts": [], "task_slices": []}}
            if str(task.agent_type) == "change_impact"
            else {"completed": True}
        )
        result = AgentResult(
            task_id=task.task_id,
            agent_type=task.agent_type,
            outcome="succeeded",
            result_ref=f"blackboard://results/{task.task_id}",
            summary=summary,
        )
        decision = await service.commit_result(
            result,
            EventEnvelope(
                event_type="agent.result.committed",
                run_id=run.id,
                correlation_id=task.task_id,
                producer="test-worker",
                payload={"outcome": "succeeded"},
            ),
        )

    assert len(await service.tasks(run.id)) == 5
    assert decision is not None
    assert decision.outcome == "PASS"
    assert (await service.get(run.id)).status == "passed"


async def test_independent_fanout_leases_concurrently() -> None:
    blackboard = InMemoryBlackboard()
    registry = AgentRegistry.from_file("config/agents.yaml")
    service = RunService(DeterministicSupervisor(registry, blackboard))
    run = await service.create(RunRequest(target_id="atlas-local"))
    impact = await service.lease_task("worker-0")
    assert impact is not None
    await service.commit_result(
        AgentResult(
            task_id=impact.task_id,
            agent_type=impact.agent_type,
            outcome="succeeded",
            result_ref=f"blackboard://results/{impact.task_id}",
            summary={"charter": {"impacts": [], "task_slices": []}},
        ),
        EventEnvelope(
            event_type="agent.result.committed",
            run_id=run.id,
            correlation_id=impact.task_id,
            producer="worker-0",
            payload={"outcome": "succeeded"},
        ),
    )

    leased = [await service.lease_task(f"worker-{index}") for index in range(1, 5)]
    assert all(task is not None for task in leased)
    assert len({task.target_partition for task in leased if task is not None}) == 4
    assert await service.lease_task("worker-5") is None


async def test_concurrent_terminal_results_finalize_the_run_once() -> None:
    blackboard = InMemoryBlackboard()
    registry = AgentRegistry.from_file("config/agents.yaml")
    service = RunService(DeterministicSupervisor(registry, blackboard))
    run = await service.create(RunRequest(target_id="atlas-local"))
    impact = await service.lease_task("worker-0")
    assert impact is not None
    await service.commit_result(
        AgentResult(
            task_id=impact.task_id,
            agent_type=impact.agent_type,
            outcome="succeeded",
            result_ref=f"blackboard://results/{impact.task_id}",
            summary={"charter": {"impacts": [], "task_slices": []}},
        ),
        EventEnvelope(
            event_type="agent.result.committed",
            run_id=run.id,
            correlation_id=impact.task_id,
            producer="worker-0",
            payload={"outcome": "succeeded"},
        ),
    )

    leased = [await service.lease_task(f"worker-{index}") for index in range(1, 5)]
    assert all(task is not None for task in leased)
    terminal_tasks = [task for task in leased if task is not None]
    original_commit_result = blackboard.commit_result
    all_results_ready = asyncio.Event()
    arrivals = 0

    async def synchronized_commit_result(
        result: AgentResult,
        event: EventEnvelope,
    ) -> None:
        nonlocal arrivals
        arrivals += 1
        if arrivals == len(terminal_tasks):
            all_results_ready.set()
        await all_results_ready.wait()
        await original_commit_result(result, event)

    blackboard.commit_result = synchronized_commit_result  # type: ignore[method-assign]

    async def commit(task: AgentTask) -> None:
        await service.commit_result(
            AgentResult(
                task_id=task.task_id,
                agent_type=task.agent_type,
                outcome="succeeded",
                result_ref=f"blackboard://results/{task.task_id}",
                summary={"completed": True},
            ),
            EventEnvelope(
                event_type="agent.result.committed",
                run_id=run.id,
                correlation_id=task.task_id,
                producer="concurrent-worker",
                payload={"outcome": "succeeded"},
            ),
        )

    await asyncio.gather(*(commit(task) for task in terminal_tasks))

    gate_events = [
        event for event in await blackboard.events(run.id) if event.event_type == "gate.decided"
    ]
    assert len(gate_events) == 1
    assert (await service.get(run.id)).status == "passed"


async def test_failed_task_retries_once_and_records_rationale() -> None:
    blackboard = InMemoryBlackboard()
    registry = AgentRegistry.from_file("config/agents.yaml")
    service = RunService(DeterministicSupervisor(registry, blackboard))
    run = await service.create(RunRequest(target_id="atlas-local"))
    first = await service.lease_task("worker-a")
    assert first is not None
    await service.commit_result(
        AgentResult(
            task_id=first.task_id,
            agent_type=first.agent_type,
            outcome="failed",
            result_ref=f"blackboard://results/{first.task_id}",
            summary={"error_type": "TimeoutError"},
        ),
        EventEnvelope(
            event_type="agent.result.committed",
            run_id=run.id,
            correlation_id=first.task_id,
            producer="worker-a",
            payload={"outcome": "failed"},
        ),
    )

    retry = await service.lease_task("worker-b")
    assert retry is not None
    assert retry.attempt == 2
    assert retry.parent_task_id == first.task_id
    retry_events = [
        event
        for event in await blackboard.events(run.id)
        if event.event_type == "agent.task.retry_scheduled"
    ]
    assert retry_events[0].payload["rationale_code"] == "retryable_agent_failure"


async def test_cancel_persists_and_rehydrates_without_late_commit() -> None:
    blackboard = InMemoryBlackboard()
    registry = AgentRegistry.from_file("config/agents.yaml")
    service = RunService(DeterministicSupervisor(registry, blackboard))
    run = await service.create(RunRequest(target_id="atlas-local"))
    leased = await service.lease_task("worker-a")
    assert leased is not None
    cancelled = await service.cancel(run.id)
    assert cancelled.status == "cancelled"

    restored = RunService(DeterministicSupervisor(registry, blackboard))
    await restored.restore()
    assert (await restored.get(run.id)).cancellation_requested
    assert {task.status for task in await restored.tasks(run.id)} == {"cancelled"}
    await restored.commit_result(
        AgentResult(
            task_id=leased.task_id,
            agent_type=leased.agent_type,
            outcome="succeeded",
            result_ref=f"blackboard://results/{leased.task_id}",
            summary={},
        ),
        EventEnvelope(
            event_type="agent.result.committed",
            run_id=run.id,
            correlation_id=leased.task_id,
            producer="worker-a",
            payload={"outcome": "succeeded"},
        ),
    )
    assert await blackboard.result(leased.task_id) is None
