from __future__ import annotations

import httpx

from apps.control_api.main import app as control_app
from apps.qa_worker.agent_host import build_runtime
from apps.qa_worker.remote_blackboard import RemoteBlackboard
from apps.qa_worker.tools import build_tools
from atlas.api.main import app as atlas_app
from atlas.api.main import state
from atlas.api.state import FAULTS
from sentinelqa.domain.models import AgentTask


async def execute_until_idle(control: httpx.AsyncClient, atlas: httpx.AsyncClient) -> None:
    blackboard = RemoteBlackboard(control)
    runtime = build_runtime(blackboard, build_tools(atlas, control))
    while True:
        response = await control.post("/internal/worker/tasks/lease", json={"worker": "e2e-worker"})
        if response.status_code == 204:
            return
        response.raise_for_status()
        task = AgentTask.model_validate(response.json())
        blackboard.select_task(task.task_id)
        await runtime.execute_task(task)


async def test_faulty_profile_replays_reports_and_blocks_then_fixed_passes(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_TEST_MODE", "true")
    monkeypatch.setenv("ATLAS_TEST_CONTROL_TOKEN", "local-test-token")
    monkeypatch.setenv("SENTINEL_WORKER_TOKEN", "e2e-worker-token")
    control_transport = httpx.ASGITransport(app=control_app)
    atlas_transport = httpx.ASGITransport(app=atlas_app)
    async with (
        httpx.AsyncClient(
            transport=control_transport,
            base_url="http://control",
            headers={"x-worker-token": "e2e-worker-token"},
        ) as control,
        httpx.AsyncClient(transport=atlas_transport, base_url="http://atlas") as atlas,
    ):
        state.seed(41021)
        state.faults = set(FAULTS)
        faulty = await control.post("/v1/runs", json={"target_id": "atlas-local", "seed": 41021})
        faulty.raise_for_status()
        faulty_id = faulty.json()["id"]
        await execute_until_idle(control, atlas)

        faulty_summary = (await control.get(f"/v1/runs/{faulty_id}")).json()
        faulty_gate = (await control.get(f"/v1/gates/{faulty_id}")).json()
        defects = (await control.get(f"/v1/runs/{faulty_id}/defects")).json()
        assert faulty_summary["run"]["status"] == "blocked"
        assert faulty_gate["outcome"] == "BLOCK"
        assert faulty_gate["metrics"]["verified_critical_defects"] == 3
        assert len(defects) == 9
        assert all(item["verification"] == {"attempts": 2, "matched": 2} for item in defects)
        assert all(len(item["evidence"]) == 3 for item in defects)
        event_types = {
            item["event_type"]
            for item in (
                await control.get(f"/v1/runs/{faulty_id}/events", params={"limit": 500})
            ).json()["items"]
        }
        for prefix in (
            "run.",
            "agent.task.",
            "agent.execution.",
            "agent.result.",
            "agent.arbitration.",
            "action.",
            "anomaly.",
            "verification.",
            "defect.",
            "gate.",
        ):
            assert any(item.startswith(prefix) for item in event_types), (prefix, event_types)
        assert {"state.observed", "coverage.updated"}.issubset(event_types)

        state.seed(41021)
        state.faults.clear()
        fixed = await control.post(
            "/v1/runs",
            json={"target_id": "atlas-local", "trigger": "verification", "seed": 41021},
        )
        fixed.raise_for_status()
        fixed_id = fixed.json()["id"]
        await execute_until_idle(control, atlas)
        fixed_summary = (await control.get(f"/v1/runs/{fixed_id}")).json()
        fixed_gate = (await control.get(f"/v1/gates/{fixed_id}")).json()
        assert fixed_summary["run"]["status"] == "passed"
        assert fixed_summary["defect_count"] == 0
        assert fixed_gate["outcome"] == "PASS"
