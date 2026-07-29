from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sentinelqa.adapters import (
    ProjectAdapter,
    find_application_path,
    load_project_adapter,
    resolve_config_reference,
)
from sentinelqa.blackboard.context_projection import project_context
from sentinelqa.blackboard.port import Blackboard
from sentinelqa.config import load_yaml
from sentinelqa.domain.enums import AgentType
from sentinelqa.domain.models import AgentResult, AgentTask, Anomaly, Run, VerificationResult

from .agent_registry import AgentRegistry


class DeterministicSupervisor:
    def __init__(
        self,
        registry: AgentRegistry,
        blackboard: Blackboard,
        application_directory: str | Path = "config/applications",
    ) -> None:
        self.registry = registry
        self.blackboard = blackboard
        self.application_directory = Path(application_directory)
        self._task_signatures: dict[UUID, set[str]] = {}
        self._anomaly_signatures: dict[UUID, set[str]] = {}

    def project_adapter(self, target_id: str) -> ProjectAdapter:
        path = find_application_path(target_id, self.application_directory)
        return load_project_adapter(path)

    def _application_path(self, target_id: str) -> Path:
        return find_application_path(target_id, self.application_directory)

    def restore_task(self, task: AgentTask) -> None:
        self._task_signatures.setdefault(task.run_id, set()).add(task.idempotency_key)

    def restore_anomalies(self, anomalies: Iterable[Anomaly]) -> None:
        for anomaly in anomalies:
            self._anomaly_signatures.setdefault(anomaly.run_id, set()).add(anomaly.signature)

    async def _task(
        self,
        run: Run,
        agent_type: AgentType,
        source: dict[str, Any],
        partition: str,
        parent: UUID | None = None,
        dependencies: tuple[UUID, ...] = (),
        signature_extra: str = "",
    ) -> AgentTask | None:
        manifest = self.registry.manifest(str(agent_type))
        key = AgentTask.stable_key(run.id, agent_type, partition, signature_extra)
        seen = self._task_signatures.setdefault(run.id, set())
        if key in seen:
            return None
        seen.add(key)
        projected = project_context(str(agent_type), source)
        ref = await self.blackboard.put_context(key.removeprefix("sha256:"), projected)
        return AgentTask(
            run_id=run.id,
            parent_task_id=parent,
            agent_type=agent_type,
            dependency_task_ids=dependencies,
            input_ref=ref,
            idempotency_key=key,
            target_partition=partition,
            budget=manifest.budget,
            deadline=datetime.now(UTC) + timedelta(seconds=manifest.budget.max_seconds),
        )

    async def plan_initial_tasks(self, run: Run) -> list[AgentTask]:
        application = self.project_adapter(run.request.target_id).application
        source = {
            "changed_files": list(run.request.changed_files),
            "changed_operations": list(run.request.changed_operations),
            "coverage_gaps": list(run.request.coverage_gaps),
            "critical_journeys": list(application.critical_journeys),
        }
        task = await self._task(
            run,
            AgentType.CHANGE_IMPACT,
            source,
            f"{run.request.target_id}:read-only",
        )
        return [task] if task else []

    async def handle_result(self, run: Run, result: AgentResult) -> list[AgentTask]:
        if str(result.agent_type) != AgentType.CHANGE_IMPACT:
            return []
        charter = result.summary.get("charter", {})
        adapter = self.project_adapter(run.request.target_id)
        application = adapter.application
        app_path = self._application_path(run.request.target_id)
        base_parts = urlsplit(application.base_url)
        allowed_origin = f"{base_parts.scheme}://{base_parts.netloc}"
        tasks: list[AgentTask] = []
        for persona in run.request.personas:
            for browser in run.request.browsers:
                source = {
                    "run_id": str(run.id),
                    "charter": charter,
                    "persona": persona,
                    "browser": browser,
                    "browser_settings": run.request.browser_settings.model_dump(mode="json"),
                    "seed": run.request.seed,
                    "target_id": application.id,
                    "base_url": application.base_url,
                    "allowed_origin": allowed_origin,
                    "allowed_origins": list(application.allowed_origins),
                }
                task = await self._task(
                    run,
                    AgentType.UI_EXPLORER,
                    source,
                    f"{run.request.target_id}:{persona}:{browser}",
                    result.task_id,
                    (result.task_id,),
                    f"{persona}:{browser}",
                )
                if task:
                    tasks.append(task)
        api = await self._task(
            run,
            AgentType.API_EXPLORER,
            {
                "charter": charter,
                "seed": run.request.seed,
                "openapi": {},
                "target_id": application.id,
                "persona": run.request.personas[0],
            },
            f"{run.request.target_id}:api",
            result.task_id,
            (result.task_id,),
        )
        invariant_items: list[dict[str, Any]] = []
        invariant_reference = application.invariant_file or application.legacy_invariant_file
        if invariant_reference:
            invariant_path = resolve_config_reference(app_path, invariant_reference)
            invariant_items = list(load_yaml(invariant_path).get("invariants", []))
        data = await self._task(
            run,
            AgentType.DATA_INVARIANT,
            {
                "entities": [],
                "observations": [],
                "invariants": [{**item, "seed": run.request.seed} for item in invariant_items],
                "target_id": application.id,
            },
            f"{run.request.target_id}:read-only",
            result.task_id,
            (result.task_id,),
        )
        return tasks + ([api] if api else []) + ([data] if data else [])

    async def verification_task(
        self, run: Run, anomaly: Anomaly, replay_steps: list[dict[str, Any]]
    ) -> AgentTask | None:
        seen = self._anomaly_signatures.setdefault(run.id, set())
        if anomaly.signature in seen:
            return None
        seen.add(anomaly.signature)
        return await self._task(
            run,
            AgentType.VERIFICATION,
            {
                "anomaly": anomaly.model_dump(mode="json"),
                "evidence_index": list(anomaly.evidence_refs),
                "replay_steps": replay_steps,
                "replay_type": (
                    "api"
                    if any(
                        step.get("type") in {"api_request", "http_invariant"}
                        for step in replay_steps
                    )
                    else "ui"
                ),
                "seed": run.request.seed,
                "target_id": run.request.target_id,
            },
            f"{run.request.target_id}:exclusive-reset",
            signature_extra=anomaly.signature,
        )

    async def triage_task(
        self, run: Run, anomaly: Anomaly, verification: VerificationResult
    ) -> AgentTask | None:
        return await self._task(
            run,
            AgentType.DEFECT_TRIAGE,
            {
                "verification": verification.model_dump(mode="json"),
                "anomaly": anomaly.model_dump(mode="json"),
                "evidence_index": list(verification.evidence_refs),
                "defect_history": [],
            },
            f"{run.request.target_id}:read-only",
            signature_extra=anomaly.signature,
        )
