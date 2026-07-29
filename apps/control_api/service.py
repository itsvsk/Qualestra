from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sentinelqa.config import load_yaml
from sentinelqa.delivery import DeliveryOrchestrator, build_delivery_registry_from_env
from sentinelqa.domain.enums import (
    AgentType,
    DeliveryMode,
    GateOutcome,
    ReportFormat,
    ReportKind,
    RunStatus,
    Severity,
    TaskStatus,
)
from sentinelqa.domain.events import EventEnvelope
from sentinelqa.domain.models import (
    AgentResult,
    AgentTask,
    Anomaly,
    Defect,
    DeliveryRecord,
    GateDecision,
    Observation,
    ReleaseSummary,
    Run,
    RunReport,
    RunRequest,
    Transition,
    VerificationResult,
    stable_digest,
)
from sentinelqa.gates.evaluator import GateEvaluator
from sentinelqa.orchestration.supervisor import DeterministicSupervisor
from sentinelqa.reporting import (
    build_defect,
    generate_run_report,
    render_report_html,
    render_report_json,
    render_report_markdown,
    report_source_fingerprint,
)


class RunService:
    def __init__(self, supervisor: DeterministicSupervisor) -> None:
        self.supervisor = supervisor
        self._runs: dict[UUID, Run] = {}
        self._tasks: dict[UUID, list[AgentTask]] = {}
        self._defects: dict[UUID, list[Defect]] = {}
        self._gates: dict[UUID, GateDecision] = {}
        self._verification_anomalies: dict[UUID, Anomaly] = {}
        self._triage_inputs: dict[UUID, tuple[Anomaly, VerificationResult]] = {}
        self._verification_results: dict[UUID, VerificationResult] = {}
        self._lock = asyncio.Lock()
        self._finalize_locks: dict[UUID, asyncio.Lock] = {}
        self.delivery_orchestrator = DeliveryOrchestrator(
            supervisor.blackboard, build_delivery_registry_from_env()
        )

    async def restore(self) -> None:
        """Rehydrate API-owned projections from the durable blackboard after a restart."""
        state = await self.supervisor.blackboard.load_state()
        if not state.runs:
            return
        runs = {run.id: deepcopy(run) for run in state.runs}
        tasks: dict[UUID, list[AgentTask]] = {run_id: [] for run_id in runs}
        defects: dict[UUID, list[Defect]] = {run_id: [] for run_id in runs}
        for task in state.tasks:
            tasks[task.run_id].append(deepcopy(task))
        for defect in state.defects:
            defects[defect.run_id].append(deepcopy(defect))
        async with self._lock:
            self._runs = runs
            self._tasks = tasks
            self._defects = defects
            self._gates = {gate.run_id: deepcopy(gate) for gate in state.gates}
        results = {result.task_id: result for result in state.results}
        anomalies = {anomaly.id: anomaly for anomaly in state.anomalies}
        for task in state.tasks:
            self.supervisor.restore_task(task)
            if task.agent_type not in {AgentType.VERIFICATION, AgentType.DEFECT_TRIAGE}:
                continue
            context = await self.supervisor.blackboard.get_context(task.input_ref)
            anomaly = Anomaly.model_validate(context["anomaly"])
            anomalies.setdefault(anomaly.id, anomaly)
            if task.agent_type == AgentType.VERIFICATION:
                self._verification_anomalies[task.task_id] = anomaly
                result = results.get(task.task_id)
                if result is not None and result.outcome == "succeeded":
                    self._verification_results[anomaly.id] = self._verification_from_result(
                        anomaly, result
                    )
            else:
                verification = VerificationResult.model_validate(context["verification"])
                self._triage_inputs[task.task_id] = (anomaly, verification)
        self.supervisor.restore_anomalies(anomalies.values())
        await self.delivery_orchestrator.recover()

    async def create(self, request: RunRequest) -> Run:
        adapter = self.supervisor.project_adapter(request.target_id)
        unknown_personas = sorted(set(request.personas) - set(adapter.personas()))
        if unknown_personas:
            raise ValueError(f"unknown personas for {request.target_id}: {unknown_personas}")
        run = Run(request=request)
        run.status = RunStatus.PREPARING
        run.started_at = datetime.now(UTC)
        tasks = await self.supervisor.plan_initial_tasks(run)
        run.status = RunStatus.EXPLORING
        await self.supervisor.blackboard.register_run(run, tasks)
        async with self._lock:
            self._runs[run.id] = deepcopy(run)
            self._tasks[run.id] = tasks
            self._defects[run.id] = []
        await self.supervisor.blackboard.append_event(
            EventEnvelope(
                event_type="run.created",
                run_id=run.id,
                correlation_id=run.id,
                producer="control-api",
                payload={"trigger": request.trigger, "initial_task_count": len(tasks)},
            )
        )
        return deepcopy(run)

    async def get(self, run_id: UUID) -> Run:
        async with self._lock:
            return deepcopy(self._runs[run_id])

    async def list_runs(self) -> list[Run]:
        async with self._lock:
            return sorted(
                (deepcopy(run) for run in self._runs.values()), key=lambda run: str(run.id)
            )

    async def cancel(self, run_id: UUID) -> Run:
        cancelled_tasks: list[UUID] = []
        async with self._lock:
            run = self._runs[run_id]
            if run.status in {
                RunStatus.PASSED,
                RunStatus.WARNED,
                RunStatus.BLOCKED,
                RunStatus.CANCELLED,
                RunStatus.FAILED,
            }:
                return deepcopy(run)
            run.cancellation_requested = True
            run.status = RunStatus.CANCELLED
            run.ended_at = datetime.now(UTC)
            self._tasks[run_id] = [
                task.model_copy(update={"status": TaskStatus.CANCELLED})
                if task.status in {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.LEASED}
                else task
                for task in self._tasks[run_id]
            ]
            cancelled_tasks = [
                task.task_id for task in self._tasks[run_id] if task.status == TaskStatus.CANCELLED
            ]
            cancelled_run = deepcopy(run)
        for task_id in cancelled_tasks:
            await self.supervisor.blackboard.update_task_status(task_id, TaskStatus.CANCELLED)
        await self.supervisor.blackboard.update_run(cancelled_run)
        await self.supervisor.blackboard.append_event(
            EventEnvelope(
                event_type="run.cancelled",
                run_id=run_id,
                correlation_id=run_id,
                producer="control-api",
                payload={"cancelled_task_count": len(cancelled_tasks)},
            )
        )
        return cancelled_run

    async def tasks(self, run_id: UUID) -> list[AgentTask]:
        async with self._lock:
            return deepcopy(self._tasks[run_id])

    async def add_tasks(self, run_id: UUID, tasks: list[AgentTask]) -> None:
        async with self._lock:
            existing = {task.idempotency_key for task in self._tasks[run_id]}
            additions = [task for task in tasks if task.idempotency_key not in existing]
            self._tasks[run_id].extend(additions)
        await self.supervisor.blackboard.register_tasks(additions)

    async def defects(self, run_id: UUID) -> list[Defect]:
        async with self._lock:
            return deepcopy(self._defects[run_id])

    async def defect(self, defect_id: UUID) -> Defect:
        async with self._lock:
            for defects in self._defects.values():
                for defect in defects:
                    if defect.defect_id == defect_id:
                        return deepcopy(defect)
        raise KeyError(defect_id)

    async def gate(self, run_id: UUID) -> GateDecision:
        async with self._lock:
            return deepcopy(self._gates[run_id])

    async def set_gate(self, decision: GateDecision) -> None:
        async with self._lock:
            self._gates[decision.run_id] = deepcopy(decision)

    async def lease_task(
        self,
        worker: str,
        agent_types: tuple[str, ...] = (),
        partition_prefixes: tuple[str, ...] = (),
    ) -> AgentTask | None:
        if self.supervisor.blackboard.durable_leasing:
            recovered = await self.supervisor.blackboard.recover_expired_tasks()
            if recovered:
                recovered_ids = set(recovered)
                async with self._lock:
                    for tasks in self._tasks.values():
                        for index, task in enumerate(tasks):
                            if task.task_id in recovered_ids:
                                tasks[index] = task.model_copy(update={"status": TaskStatus.READY})
            leased = await self.supervisor.blackboard.lease_task(
                worker, agent_types, partition_prefixes
            )
            if leased is None:
                return None
            found = False
            async with self._lock:
                for tasks in self._tasks.values():
                    for index, task in enumerate(tasks):
                        if task.task_id == leased.task_id:
                            tasks[index] = deepcopy(leased)
                            found = True
                            break
                    if found:
                        break
            if not found:
                raise KeyError(leased.task_id)
            return await self._record_lease(leased, worker)
        leased_task: AgentTask | None = None
        async with self._lock:
            for run_id in sorted(self._tasks, key=str):
                run = self._runs[run_id]
                if run.cancellation_requested:
                    continue
                by_id = {item.task_id: item for item in self._tasks[run_id]}
                active_partitions = {
                    item.target_partition
                    for item in self._tasks[run_id]
                    if item.status in {TaskStatus.LEASED, TaskStatus.RUNNING}
                }
                for index, task in enumerate(self._tasks[run_id]):
                    dependencies_succeeded = all(
                        by_id[dependency].status == TaskStatus.SUCCEEDED
                        for dependency in task.dependency_task_ids
                    )
                    if (
                        task.status == TaskStatus.READY
                        and dependencies_succeeded
                        and task.target_partition not in active_partitions
                        and (not agent_types or str(task.agent_type) in agent_types)
                        and (
                            not partition_prefixes
                            or task.target_partition.startswith(partition_prefixes)
                        )
                    ):
                        leased = task.model_copy(update={"status": TaskStatus.LEASED})
                        self._tasks[run_id][index] = leased
                        leased_task = deepcopy(leased)
                        break
                if leased_task is not None:
                    break
        if leased_task is not None:
            await self.supervisor.blackboard.update_task_status(
                leased_task.task_id, TaskStatus.LEASED
            )
            return await self._record_lease(leased_task, worker)
        return None

    async def _record_lease(self, task: AgentTask, worker: str) -> AgentTask:
        await self.supervisor.blackboard.append_event(
            EventEnvelope(
                event_type="agent.task.leased",
                run_id=task.run_id,
                correlation_id=task.task_id,
                producer="control-api",
                payload={
                    "worker": worker,
                    "agent_type": str(task.agent_type),
                    "attempt": task.attempt,
                    "target_partition": task.target_partition,
                },
            )
        )
        return deepcopy(task)

    async def context_for_task(self, task_id: UUID) -> dict[str, Any]:
        async with self._lock:
            task = next(
                (
                    item
                    for tasks in self._tasks.values()
                    for item in tasks
                    if item.task_id == task_id
                ),
                None,
            )
        if task is None:
            raise KeyError(task_id)
        return await self.supervisor.blackboard.get_context(task.input_ref)

    async def commit_result(self, result: AgentResult, event: EventEnvelope) -> GateDecision | None:
        task: AgentTask | None = None
        run: Run | None = None
        async with self._lock:
            for run_id, tasks in self._tasks.items():
                for index, candidate in enumerate(tasks):
                    if candidate.task_id == result.task_id:
                        if self._runs[run_id].cancellation_requested:
                            return None
                        existing = await self.supervisor.blackboard.result(result.task_id)
                        if existing is not None:
                            return deepcopy(self._gates.get(run_id))
                        task_status = (
                            TaskStatus.SUCCEEDED
                            if result.outcome == "succeeded"
                            else TaskStatus.FAILED
                        )
                        task = candidate
                        tasks[index] = candidate.model_copy(update={"status": task_status})
                        run = deepcopy(self._runs[run_id])
                        break
                if task is not None:
                    break
        if task is None or run is None:
            raise KeyError(result.task_id)

        await self.supervisor.blackboard.commit_result(result, event)
        await self.supervisor.blackboard.append_event(
            EventEnvelope(
                event_type="agent.execution.completed",
                run_id=run.id,
                correlation_id=result.task_id,
                producer="control-api",
                payload={"agent_type": str(result.agent_type), "outcome": result.outcome},
            )
        )
        retry = await self._retry_task(task, result)
        if retry is not None:
            await self.add_tasks(run.id, [retry])
            await self.supervisor.blackboard.append_event(
                EventEnvelope(
                    event_type="agent.task.retry_scheduled",
                    run_id=run.id,
                    correlation_id=retry.task_id,
                    producer="control-api",
                    payload={
                        "previous_task_id": str(task.task_id),
                        "attempt": retry.attempt,
                        "rationale_code": "retryable_agent_failure",
                    },
                )
            )
            return None
        new_tasks = await self.supervisor.handle_result(run, result)
        new_tasks.extend(await self._result_follow_ups(run, task, result))
        if new_tasks:
            await self.add_tasks(run.id, new_tasks)
            await self.supervisor.blackboard.append_event(
                EventEnvelope(
                    event_type="agent.arbitration.routed",
                    run_id=run.id,
                    correlation_id=result.task_id,
                    producer="control-api",
                    payload={"task_count": len(new_tasks)},
                )
            )

        async with self._lock:
            current_tasks = self._tasks[run.id]
            all_terminal = all(
                item.status
                in {
                    TaskStatus.SUCCEEDED,
                    TaskStatus.FAILED,
                    TaskStatus.TIMED_OUT,
                    TaskStatus.CANCELLED,
                    TaskStatus.SUPERSEDED,
                }
                for item in current_tasks
            )
        if not all_terminal:
            return None
        return await self._finalize(run.id)

    async def _retry_task(self, task: AgentTask, result: AgentResult) -> AgentTask | None:
        manifest = self.supervisor.registry.manifest(str(task.agent_type), task.agent_version)
        if result.outcome != "failed" or task.attempt >= manifest.max_attempts:
            return None
        retry = task.model_copy(
            update={
                "task_id": uuid4(),
                "parent_task_id": task.task_id,
                "status": TaskStatus.READY,
                "dependency_task_ids": (),
                "idempotency_key": AgentTask.stable_key(task.idempotency_key, task.attempt + 1),
                "deadline": datetime.now(UTC) + timedelta(seconds=manifest.budget.max_seconds),
                "attempt": task.attempt + 1,
            }
        )
        # model_copy deliberately bypasses validation; rebuild to validate the retry contract.
        retry = AgentTask.model_validate(retry.model_dump(mode="json"))
        if task.agent_type == AgentType.VERIFICATION:
            self._verification_anomalies[retry.task_id] = self._verification_anomalies[task.task_id]
        elif task.agent_type == AgentType.DEFECT_TRIAGE:
            self._triage_inputs[retry.task_id] = self._triage_inputs[task.task_id]
        self.supervisor.restore_task(retry)
        return retry

    @staticmethod
    def _verification_from_result(anomaly: Anomaly, result: AgentResult) -> VerificationResult:
        summary = result.summary
        return VerificationResult(
            anomaly_id=anomaly.id,
            attempts=int(summary["attempts"]),
            matched=int(summary["matched"]),
            minimized_steps=tuple(summary["minimized_steps"]),
            confidence=float(summary["confidence"]),
            verified=bool(summary["verified"]),
            evidence_refs=tuple(dict.fromkeys((*anomaly.evidence_refs, *result.evidence_refs))),
        )

    async def _result_follow_ups(
        self, run: Run, task: AgentTask, result: AgentResult
    ) -> list[AgentTask]:
        if result.outcome != "succeeded":
            return []
        if task.agent_type == AgentType.UI_EXPLORER:
            for raw in result.summary.get("observations", []):
                observation = Observation.model_validate(raw)
                if observation.run_id != run.id:
                    raise ValueError("observation belongs to a different run")
                await self.supervisor.blackboard.put_observation(observation)
                await self.supervisor.blackboard.append_event(
                    EventEnvelope(
                        event_type="state.observed",
                        run_id=run.id,
                        correlation_id=task.task_id,
                        producer="control-api",
                        payload={
                            "fingerprint": observation.state_fingerprint,
                            "route": observation.page.route,
                        },
                    )
                )
            for raw in result.summary.get("transitions", []):
                transition = Transition.model_validate(raw)
                await self.supervisor.blackboard.put_transition(run.id, transition)
                await self.supervisor.blackboard.append_event(
                    EventEnvelope(
                        event_type="action.executed",
                        run_id=run.id,
                        correlation_id=transition.action_id,
                        producer="control-api",
                        payload=transition.model_dump(mode="json"),
                    )
                )
            await self.supervisor.blackboard.append_event(
                EventEnvelope(
                    event_type="coverage.updated",
                    run_id=run.id,
                    correlation_id=task.task_id,
                    producer="control-api",
                    payload=await self.supervisor.blackboard.coverage(run.id),
                )
            )
            return await self._schedule_discovery_anomalies(run, task, result)
        if task.agent_type in {AgentType.API_EXPLORER, AgentType.DATA_INVARIANT}:
            return await self._schedule_discovery_anomalies(run, task, result)
        if task.agent_type == AgentType.VERIFICATION:
            anomaly = self._verification_anomalies[task.task_id]
            verification_result = self._verification_from_result(anomaly, result)
            await self.supervisor.blackboard.append_event(
                EventEnvelope(
                    event_type="verification.completed",
                    run_id=run.id,
                    correlation_id=task.task_id,
                    producer="control-api",
                    payload={
                        "anomaly_id": str(anomaly.id),
                        "verified": verification_result.verified,
                        "confidence": verification_result.confidence,
                        "failure_class": result.summary.get("failure_class"),
                    },
                )
            )
            self._verification_results[anomaly.id] = verification_result
            if not verification_result.verified:
                return []
            triage = await self.supervisor.triage_task(run, anomaly, verification_result)
            if triage is None:
                return []
            self._triage_inputs[triage.task_id] = (anomaly, verification_result)
            return [triage]
        if task.agent_type == AgentType.DEFECT_TRIAGE:
            anomaly, verification_result = self._triage_inputs[task.task_id]
            if result.summary.get("disposition") != "new_defect":
                return []
            defect = build_defect(
                run_id=run.id,
                anomaly=anomaly,
                verification=verification_result,
                persona="systematic-role-matrix",
                affected_area=anomaly.route,
                suggested_investigation=(
                    f"Inspect {anomaly.suspected_component} for invariant {anomaly.signal}."
                ),
            )
            async with self._lock:
                existing = {item.signature for item in self._defects[run.id]}
                if defect.signature not in existing:
                    self._defects[run.id].append(defect)
            await self.supervisor.blackboard.put_defect(defect)
            await self.supervisor.blackboard.append_event(
                EventEnvelope(
                    event_type="defect.verified",
                    run_id=run.id,
                    correlation_id=defect.defect_id,
                    producer="control-api",
                    payload={
                        "severity": str(defect.severity),
                        "confidence": defect.confidence,
                        "signature": defect.signature,
                    },
                )
            )
        return []

    async def _schedule_discovery_anomalies(
        self, run: Run, task: AgentTask, result: AgentResult
    ) -> list[AgentTask]:
        output: list[AgentTask] = []
        for raw in result.summary.get("anomalies", []):
            anomaly = Anomaly(
                id=UUID(str(raw["anomaly_id"])),
                run_id=run.id,
                oracle_type=str(raw["oracle_type"]),
                route=str(raw["route"]),
                action_type=str(raw["action_type"]),
                signal=str(raw["signal"]),
                expected=str(raw["expected"]),
                actual=str(raw["actual"]),
                severity_hint=Severity(str(raw["severity_hint"])),
                evidence_refs=tuple(str(item) for item in raw["evidence_refs"]),
                suspected_component=str(raw["suspected_component"]),
            )
            await self.supervisor.blackboard.put_anomaly(anomaly)
            await self.supervisor.blackboard.append_event(
                EventEnvelope(
                    event_type="anomaly.detected",
                    run_id=run.id,
                    correlation_id=anomaly.id,
                    producer="control-api",
                    payload={"signature": anomaly.signature, "signal": anomaly.signal},
                )
            )
            verification = await self.supervisor.verification_task(
                run, anomaly, list(raw["replay_steps"])
            )
            if verification is not None:
                self._verification_anomalies[verification.task_id] = anomaly
                output.append(verification)
                await self.supervisor.blackboard.append_event(
                    EventEnvelope(
                        event_type="anomaly.verification.planned",
                        run_id=run.id,
                        correlation_id=verification.task_id,
                        producer="control-api",
                        payload={"anomaly_id": str(anomaly.id), "signature": anomaly.signature},
                    )
                )
        return output

    async def _finalize(self, run_id: UUID) -> GateDecision:
        async with self._lock:
            existing = self._gates.get(run_id)
            if existing is not None:
                return deepcopy(existing)
            finalize_lock = self._finalize_locks.setdefault(run_id, asyncio.Lock())

        async with finalize_lock:
            async with self._lock:
                existing = self._gates.get(run_id)
                if existing is not None:
                    return deepcopy(existing)
            return await self._finalize_once(run_id)

    async def _finalize_once(self, run_id: UUID) -> GateDecision:
        tasks = await self.tasks(run_id)
        defects = await self.defects(run_id)
        failed = sum(task.status != TaskStatus.SUCCEEDED for task in tasks)
        api_tasks = [task for task in tasks if str(task.agent_type) == "api_explorer"]
        verified_critical = sum(item.severity == Severity.CRITICAL for item in defects)
        verified_high_sensitive = sum(
            item.severity == Severity.HIGH
            and any(marker in item.signature for marker in ("authorization", "tenant", "integrity"))
            for item in defects
        )
        run_anomaly_ids = {
            anomaly.id
            for anomaly in self._verification_anomalies.values()
            if anomaly.run_id == run_id
        }
        verification_values = [
            item
            for anomaly_id, item in self._verification_results.items()
            if anomaly_id in run_anomaly_ids
        ]
        reproduction_rate = (
            sum(item.matched / item.attempts for item in verification_values)
            / len(verification_values)
            if verification_values
            else 1.0
        )
        metrics: dict[str, Any] = {
            "verified_critical_defects": verified_critical,
            "verified_high_authorization_or_data_integrity_defects": verified_high_sensitive,
            "critical_journey_success_rate": 1.0 if failed == 0 else 0.0,
            "required_contract_checks_passed": bool(api_tasks)
            and all(task.status == TaskStatus.SUCCEEDED for task in api_tasks),
            "policy_violations_executed": 0,
            "changed_feature_transition_coverage": 1.0 if failed == 0 else 0.0,
            "reproduction_rate": reproduction_rate,
            "environment_error_rate": 0.0 if failed == 0 else 1.0,
            "critical_journey_score": 1.0 if failed == 0 else 0.0,
            "changed_feature_coverage": 1.0 if failed == 0 else 0.0,
            "api_contract_score": 1.0 if api_tasks and failed == 0 else 0.0,
            "verified_defect_score": 1.0,
            "role_and_tenant_score": 0.0
            if any(
                marker in item.signature
                for item in defects
                for marker in ("authorization", "tenant")
            )
            else 1.0,
            "reliability_score": 1.0 if failed == 0 else 0.0,
            "accessibility_score": 0.0
            if any("accessibility" in item.signature for item in defects)
            else 1.0,
        }
        configuration = load_yaml("config/quality-gates.yaml")
        decision = GateEvaluator().evaluate(
            run_id, metrics, configuration["profiles"]["pull_request"]
        )
        async with self._lock:
            self._gates[run_id] = deepcopy(decision)
            run = self._runs[run_id]
            run.status = {
                GateOutcome.PASS: RunStatus.PASSED,
                GateOutcome.WARN: RunStatus.WARNED,
                GateOutcome.BLOCK: RunStatus.BLOCKED,
            }[decision.outcome]
            run.ended_at = datetime.now(UTC)
        await self.supervisor.blackboard.put_gate(decision)
        await self.supervisor.blackboard.update_run(await self.get(run_id))
        await self.supervisor.blackboard.append_event(
            EventEnvelope(
                event_type="gate.decided",
                run_id=run_id,
                correlation_id=run_id,
                producer="control-api",
                payload={"outcome": str(decision.outcome)},
            )
        )
        return decision

    async def summary(self, run_id: UUID) -> dict[str, Any]:
        run = await self.get(run_id)
        return {
            "run": run.model_dump(mode="json"),
            "task_count": len(await self.tasks(run_id)),
            "defect_count": len(await self.defects(run_id)),
        }

    async def report_bundle(self, run_id: UUID, *, regenerate: bool = False) -> dict[str, Any]:
        run = await self.get(run_id)
        try:
            gate = await self.gate(run_id)
        except KeyError as error:
            raise ValueError(
                "reports are available only after the release gate is decided"
            ) from error
        defects = await self.defects(run_id)
        document = generate_run_report(run, defects, gate)
        source_fingerprint = report_source_fingerprint(run, defects, gate)
        renderers = {
            ReportFormat.JSON: ("application/json", render_report_json),
            ReportFormat.MARKDOWN: ("text/markdown; charset=utf-8", render_report_markdown),
            ReportFormat.HTML: ("text/html; charset=utf-8", render_report_html),
        }
        before = await self.supervisor.blackboard.reports(run_id)
        stored: list[RunReport] = []
        for report_format, (_, renderer) in renderers.items():
            content = renderer(document)
            report = RunReport(
                report_id=uuid5(
                    NAMESPACE_URL,
                    f"sentinelqa:{run_id}:{source_fingerprint}:{report_format}",
                ),
                run_id=run_id,
                kind=ReportKind.TECHNICAL,
                format=report_format,
                content=content,
                digest=stable_digest(content),
                source_fingerprint=source_fingerprint,
                generated_at=document.generated_at,
            )
            stored.append(await self.supervisor.blackboard.put_run_report(report))

        by_format = {ReportFormat(item.format): item for item in stored}
        severity_counts = document.technical_summary.severity_counts
        coverage = await self.supervisor.blackboard.coverage(run_id)
        summary = ReleaseSummary(
            summary_id=uuid5(
                NAMESPACE_URL,
                f"sentinelqa-summary:{run_id}:{source_fingerprint}",
            ),
            run_id=run_id,
            report_id=by_format[ReportFormat.JSON].report_id,
            run_status=run.status,
            gate_outcome=gate.outcome,
            verified_defects=document.technical_summary.verified_defect_count,
            defects_by_severity={
                "critical": severity_counts.critical,
                "high": severity_counts.high,
                "medium": severity_counts.medium,
                "low": severity_counts.low,
            },
            coverage_metrics={
                key: value
                for key, value in coverage.items()
                if key != "graph" and isinstance(value, (int, float))
            },
            blockers=gate.blockers,
            release_confidence=gate.release_confidence,
            source_fingerprint=source_fingerprint,
            generated_at=document.generated_at,
        )
        await self.supervisor.blackboard.put_release_summary(summary)
        source_was_stored = any(item.source_fingerprint == source_fingerprint for item in before)
        if regenerate or not source_was_stored:
            await self.supervisor.blackboard.append_event(
                EventEnvelope(
                    event_type="report.regenerated" if regenerate else "report.generated",
                    run_id=run_id,
                    correlation_id=summary.summary_id,
                    producer="control-api",
                    payload={
                        "source_fingerprint": source_fingerprint,
                        "formats": [str(item) for item in renderers],
                    },
                )
            )
        return {
            "run_id": str(run_id),
            "schema_version": document.schema_version,
            "generated_at": document.generated_at.isoformat(),
            "title": document.executive_summary.headline,
            "summary": document.executive_summary.narrative,
            "gate_outcome": document.release_summary.outcome,
            "verified_defect_count": document.technical_summary.verified_defect_count,
            "documents": [
                {
                    "report_id": str(report.report_id),
                    "format": str(report.format),
                    "media_type": renderers[ReportFormat(report.format)][0],
                    "content": report.content,
                    "digest": report.digest,
                }
                for report in sorted(stored, key=lambda item: str(item.format))
            ],
        }

    async def create_delivery(
        self,
        *,
        run_id: UUID,
        report_id: UUID,
        connector_id: str,
        destination_id: str,
        mode: DeliveryMode,
    ) -> DeliveryRecord:
        await self.get(run_id)
        report = await self.supervisor.blackboard.run_report(report_id)
        if report is None:
            raise KeyError(report_id)
        return await self.delivery_orchestrator.create(
            run_id=run_id,
            report=report,
            connector_id=connector_id,
            destination_id=destination_id,
            mode=mode,
        )

    def destination_catalog(self) -> dict[str, list[dict[str, object]]]:
        """Return the explicit non-secret subset of configured destination metadata."""
        return {
            "items": [
                {
                    "connector_id": destination.connector_id,
                    "destination_id": destination.destination_id,
                    "display_name": destination.display_name,
                    "kind": destination.kind,
                    "provider": destination.provider,
                    "supported_modes": destination.supported_modes,
                }
                for destination in self.delivery_orchestrator.registry.catalog()
            ]
        }

    async def deliveries(self, run_id: UUID) -> list[dict[str, Any]]:
        await self.get(run_id)
        records = await self.supervisor.blackboard.deliveries(run_id)
        return [await self.delivery_view(record) for record in records]

    async def approve_delivery(self, delivery_id: UUID) -> dict[str, Any]:
        record = await self.delivery_orchestrator.approve(delivery_id)
        return await self.delivery_view(record)

    async def retry_delivery(self, delivery_id: UUID) -> dict[str, Any]:
        record = await self.delivery_orchestrator.retry(delivery_id)
        return await self.delivery_view(record)

    async def delivery_view(self, record: DeliveryRecord) -> dict[str, Any]:
        attempts = await self.supervisor.blackboard.delivery_attempts(record.request.delivery_id)
        latest = attempts[-1] if attempts else None
        external_reference = None
        external_url = None
        if latest is not None:
            external_reference = latest.external_id
            external_url = latest.external_url
        return {
            "delivery_id": str(record.request.delivery_id),
            "run_id": str(record.request.run_id),
            "report_id": str(record.request.report_id),
            "connector_id": record.request.connector_id,
            "destination_id": record.request.destination_id,
            "mode": str(record.request.mode),
            "status": str(record.status),
            "created_at": record.request.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "attempts": [
                {
                    "attempt": item.attempt,
                    "status": item.status,
                    "recorded_at": item.recorded_at.isoformat(),
                    "error": (item.error.redacted_message if item.error is not None else None),
                }
                for item in attempts
            ],
            "external_reference": external_reference,
            "external_url": external_url,
        }
