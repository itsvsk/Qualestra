from sentinelqa.blackboard.memory import InMemoryBlackboard
from sentinelqa.domain.models import Run, RunRequest
from sentinelqa.orchestration.agent_registry import AgentRegistry
from sentinelqa.orchestration.agent_runtime import AgentRuntime
from sentinelqa.orchestration.supervisor import DeterministicSupervisor


async def test_supervisor_fans_out_static_tasks_and_deduplicates():
    registry = AgentRegistry.from_file("config/agents.yaml")
    blackboard = InMemoryBlackboard()
    supervisor = DeterministicSupervisor(registry, blackboard)
    run = Run(
        request=RunRequest(target_id="atlas-local", changed_files=("atlas/api/work_items.py",))
    )
    initial = await supervisor.plan_initial_tasks(run)
    assert [str(task.agent_type) for task in initial] == ["change_impact"]
    runtime = AgentRuntime(registry, blackboard, {})
    result = await runtime.execute_task(initial[0])
    downstream = await supervisor.handle_result(run, result)
    assert {str(task.agent_type) for task in downstream} == {
        "ui_explorer",
        "api_explorer",
        "data_invariant",
    }
    assert await supervisor.handle_result(run, result) == []


async def test_runtime_commits_schema_valid_result_atomically():
    registry = AgentRegistry.from_file("config/agents.yaml")
    blackboard = InMemoryBlackboard()
    supervisor = DeterministicSupervisor(registry, blackboard)
    run = Run(request=RunRequest(target_id="atlas-local"))
    task = (await supervisor.plan_initial_tasks(run))[0]
    result = await AgentRuntime(registry, blackboard, {}).execute_task(task)
    assert result.outcome == "succeeded"
    assert await blackboard.result(task.task_id) == result
    events = await blackboard.events(run.id)
    assert events[-1].event_type == "agent.result.committed"


async def test_supervisor_creates_each_persona_browser_partition() -> None:
    registry = AgentRegistry.from_file("config/agents.yaml")
    blackboard = InMemoryBlackboard()
    supervisor = DeterministicSupervisor(registry, blackboard)
    run = Run(
        request=RunRequest(
            target_id="atlas-local",
            personas=("owner", "viewer"),
            browsers=("chromium", "firefox"),
        )
    )
    initial = (await supervisor.plan_initial_tasks(run))[0]
    result = await AgentRuntime(registry, blackboard, {}).execute_task(initial)
    downstream = await supervisor.handle_result(run, result)
    ui_tasks = [task for task in downstream if str(task.agent_type) == "ui_explorer"]
    assert {task.target_partition for task in ui_tasks} == {
        "atlas-local:owner:chromium",
        "atlas-local:owner:firefox",
        "atlas-local:viewer:chromium",
        "atlas-local:viewer:firefox",
    }


async def test_supervisor_projects_portable_target_runtime_context() -> None:
    registry = AgentRegistry.from_file("config/agents.yaml")
    blackboard = InMemoryBlackboard()
    supervisor = DeterministicSupervisor(registry, blackboard)
    run = Run(
        request=RunRequest(target_id="acme-tasks-local", personas=("viewer",), browsers=("webkit",))
    )
    initial = (await supervisor.plan_initial_tasks(run))[0]
    result = await AgentRuntime(registry, blackboard, {}).execute_task(initial)
    downstream = await supervisor.handle_result(run, result)
    contexts = {
        str(task.agent_type): await blackboard.get_context(task.input_ref) for task in downstream
    }

    assert contexts["ui_explorer"]["base_url"] == "http://acme-web:8080"
    assert contexts["ui_explorer"]["target_id"] == "acme-tasks-local"
    assert contexts["api_explorer"]["persona"] == "viewer"
    assert contexts["data_invariant"]["invariants"][0]["id"] == "service_is_ready"
