from __future__ import annotations

import asyncio
import os
import signal
import socket
from collections.abc import Mapping
from time import perf_counter

import httpx
import uvicorn

from sentinelqa.domain.events import EventEnvelope
from sentinelqa.domain.models import AgentResult, AgentTask
from sentinelqa.observability import MetricsRecorder, metrics_from_env, tracer_from_env
from sentinelqa.orchestration.worker_pools import WorkerPoolConfig
from sentinelqa.policy.tool_view import Tool

from .agent_host import build_runtime
from .remote_blackboard import RemoteBlackboard
from .tools import build_tools

METRICS = MetricsRecorder(metrics_from_env())
TRACER = tracer_from_env()


async def run_agent_loop(stop: asyncio.Event) -> None:
    worker_id = os.getenv("SENTINEL_WORKER_ID", socket.gethostname())
    pool = WorkerPoolConfig.from_env()
    concurrency = pool.capacity
    control_url = os.getenv("CONTROL_API_URL", "http://control-api:8000")
    atlas_url = os.getenv("ATLAS_API_URL", "http://atlas-api:8080")
    worker_token = os.getenv("SENTINEL_WORKER_TOKEN", "local-worker-token")
    headers = {"x-worker-token": worker_token}
    async with (
        httpx.AsyncClient(base_url=control_url, headers=headers, timeout=30) as control,
        httpx.AsyncClient(base_url=atlas_url, timeout=30) as atlas,
    ):
        tools = build_tools(atlas, control)
        registry_size = len(build_runtime(RemoteBlackboard(control), tools).registry.manifests)
        print(
            f"qa-worker ready: {registry_size} logical agents; "
            f"pool={pool.name}; concurrency={concurrency}"
        )
        active: set[asyncio.Task[None]] = set()
        while not stop.is_set():
            try:
                while len(active) < concurrency:
                    response = await control.post(
                        "/internal/worker/tasks/lease",
                        json={"worker": worker_id, **pool.task_filter.lease_payload()},
                    )
                    if response.status_code == 204:
                        break
                    response.raise_for_status()
                    task = AgentTask.model_validate(response.json())
                    active.add(
                        asyncio.create_task(
                            _execute_task(task, control, tools), name=f"agent-{task.task_id}"
                        )
                    )
                if not active:
                    await _wait_or_stop(stop)
                    continue
                done, pending = await asyncio.wait(
                    active, timeout=0.5, return_when=asyncio.FIRST_COMPLETED
                )
                active = set(pending)
                for completed in done:
                    completed.result()
            except (httpx.HTTPError, ValueError) as exc:
                print(f"qa-worker polling error: {type(exc).__name__}: {exc}")
                await _wait_or_stop(stop)
        if active:
            await asyncio.gather(*active, return_exceptions=True)


async def _wait_for_control(stop: asyncio.Event, control_url: str) -> None:
    async with httpx.AsyncClient(base_url=control_url, timeout=2) as client:
        while not stop.is_set():
            try:
                response = await client.get("/health/ready")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await _wait_or_stop(stop)
    raise RuntimeError("worker stopped before the embedded control API became ready")


async def _serve_control(server: uvicorn.Server, stop: asyncio.Event) -> None:
    try:
        await server.serve()
    finally:
        stop.set()


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    if os.getenv("SENTINEL_EMBED_CONTROL_API", "false").lower() != "true":
        await run_agent_loop(stop)
        return

    host = os.getenv("SENTINEL_CONTROL_HOST", "0.0.0.0")  # noqa: S104 - container listener
    port = int(os.getenv("SENTINEL_CONTROL_PORT", "8000"))
    control_url = os.getenv("CONTROL_API_URL", f"http://127.0.0.1:{port}")
    server = uvicorn.Server(
        uvicorn.Config(
            "apps.control_api.main:app",
            host=host,
            port=port,
            log_level=os.getenv("SENTINEL_LOG_LEVEL", "info"),
        )
    )
    async with asyncio.TaskGroup() as group:
        group.create_task(_serve_control(server, stop), name="control-api")
        await _wait_for_control(stop, control_url)
        group.create_task(run_agent_loop(stop), name="agent-runtime")
        await stop.wait()
        server.should_exit = True


async def _execute_task(
    task: AgentTask,
    control: httpx.AsyncClient,
    tools: Mapping[str, Tool],
) -> None:
    started = perf_counter()
    blackboard = RemoteBlackboard(control)
    blackboard.select_task(task.task_id)
    runtime = build_runtime(blackboard, tools)
    try:
        with TRACER.start_as_current_span(
            "sentinelqa.agent.execute",
            {
                "run.id": str(task.run_id),
                "task.id": str(task.task_id),
                "agent.type": str(task.agent_type),
                "worker.pool": os.getenv("SENTINEL_WORKER_POOL", "general"),
            },
        ):
            result = await runtime.execute_task(task)
    except Exception as exc:
        result = AgentResult(
            task_id=task.task_id,
            agent_type=task.agent_type,
            outcome="failed",
            result_ref=f"blackboard://results/{task.task_id}",
            summary={"error_type": type(exc).__name__, "error": str(exc)[:500]},
        )
        await blackboard.commit_result(
            result,
            EventEnvelope(
                event_type="agent.result.committed",
                run_id=task.run_id,
                correlation_id=task.task_id,
                producer="qa-worker",
                payload={"agent_type": str(task.agent_type), "outcome": "failed"},
            ),
        )
    METRICS.agent_task_completed(
        agent_type=str(task.agent_type),
        outcome=result.outcome,
        duration_seconds=perf_counter() - started,
        budget_used=result.budget_used,
    )


async def _wait_or_stop(stop: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=0.5)
    except TimeoutError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
