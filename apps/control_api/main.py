from __future__ import annotations

import hmac
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from sentinelqa import __version__
from sentinelqa.domain.enums import AgentType, DeliveryMode
from sentinelqa.domain.events import EventEnvelope
from sentinelqa.domain.models import AgentResult, RunRequest
from sentinelqa.orchestration.worker_pools import PartitionFilter

from .dependencies import artifacts, blackboard, database, supervisor
from .service import RunService

service = RunService(supervisor)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await service.restore()
    yield
    await service.delivery_orchestrator.aclose()
    if database is not None:
        await database.close()


app = FastAPI(title="Qualestra control API", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("SENTINEL_DASHBOARD_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)


class VerificationRunRequest(BaseModel):
    defect_id: UUID
    commit_sha: str
    target_id: str = "atlas-local"
    seed: int = 41021


class WorkerLeaseRequest(BaseModel):
    worker: str
    agent_types: tuple[str, ...] = ()
    partition_prefixes: tuple[str, ...] = ()


class WorkerCommitRequest(BaseModel):
    result: AgentResult
    event: EventEnvelope


class WorkerArtifactRequest(BaseModel):
    kind: str
    payload: dict[str, Any]


class CreateDeliveryRequest(BaseModel):
    report_id: UUID
    connector_id: str
    destination_id: str
    mode: DeliveryMode


class DeliveryDestinationView(BaseModel):
    connector_id: str
    destination_id: str
    display_name: str
    kind: Literal["artifact", "ticket", "alert"]
    provider: str
    supported_modes: tuple[DeliveryMode, ...]

    @field_validator("connector_id", "destination_id", "display_name", "provider")
    @classmethod
    def reject_sensitive_references(cls, value: str) -> str:
        normalized = value.strip().lower()
        if (
            not normalized
            or "://" in normalized
            or normalized.startswith(("env:", "secret:", "credential:"))
        ):
            raise ValueError("destination catalog metadata contains a sensitive reference")
        return value


class DeliveryDestinationCatalog(BaseModel):
    items: tuple[DeliveryDestinationView, ...]


def authorize_worker(x_worker_token: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("SENTINEL_WORKER_TOKEN", "local-worker-token")
    if not x_worker_token or not hmac.compare_digest(x_worker_token, expected):
        raise HTTPException(status_code=403, detail="invalid worker token")


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "live", "version": __version__}


@app.get("/health/ready")
async def ready() -> dict[str, object]:
    if database is not None:
        await database.ready()
    return {
        "status": "ready",
        "version": __version__,
        "registry_agents": len(supervisor.registry.manifests),
        "blackboard": "postgres" if database is not None else "memory",
    }


@app.get("/v1/runs")
async def list_runs() -> list[dict[str, Any]]:
    return [run.model_dump(mode="json") for run in await service.list_runs()]


@app.post("/v1/runs", status_code=202)
async def create_run(request: RunRequest) -> dict[str, Any]:
    try:
        run = await service.create(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return run.model_dump(mode="json")


@app.get("/v1/runs/{run_id}")
async def get_run(run_id: UUID) -> dict[str, Any]:
    try:
        return await service.summary(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@app.post("/v1/runs/{run_id}/cancel")
async def cancel_run(run_id: UUID) -> dict[str, Any]:
    try:
        return (await service.cancel(run_id)).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@app.get("/v1/runs/{run_id}/events")
async def events(
    run_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)
) -> dict[str, Any]:
    items = await blackboard.events(run_id)
    return {
        "items": [item.model_dump(mode="json") for item in items[offset : offset + limit]],
        "next_offset": offset + len(items[offset : offset + limit]),
    }


@app.get("/v1/runs/{run_id}/agents")
@app.get("/v1/runs/{run_id}/tasks")
async def tasks(run_id: UUID) -> dict[str, Any]:
    try:
        items = await service.tasks(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return {"items": [item.model_dump(mode="json") for item in items]}


@app.get("/v1/runs/{run_id}/coverage")
async def coverage(run_id: UUID) -> dict[str, Any]:
    try:
        await service.get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return await blackboard.coverage(run_id)


@app.get("/v1/runs/{run_id}/defects")
async def defects(run_id: UUID) -> list[dict[str, Any]]:
    try:
        return [item.model_dump(mode="json") for item in await service.defects(run_id)]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@app.get("/v1/defects/{defect_id}")
async def defect(defect_id: UUID) -> dict[str, Any]:
    try:
        return (await service.defect(defect_id)).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="defect not found") from exc


@app.post("/v1/verification-runs", status_code=202)
async def verification_run(request: VerificationRunRequest) -> dict[str, Any]:
    try:
        await service.defect(request.defect_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="defect not found") from exc
    try:
        run = await service.create(
            RunRequest(
                target_id=request.target_id,
                trigger="verification",
                commit_sha=request.commit_sha,
                seed=request.seed,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return run.model_dump(mode="json")


@app.get("/v1/gates/{run_id}")
async def gate(run_id: UUID) -> dict[str, Any]:
    try:
        return (await service.gate(run_id)).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="gate not available") from exc


@app.get("/v1/runs/{run_id}/report")
async def report(run_id: UUID) -> dict[str, Any]:
    try:
        return await service.report_bundle(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/runs/{run_id}/report/regenerate")
async def regenerate_report(run_id: UUID) -> dict[str, Any]:
    try:
        return await service.report_bundle(run_id, regenerate=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/delivery-destinations", response_model=DeliveryDestinationCatalog)
def delivery_destinations() -> dict[str, list[dict[str, object]]]:
    return service.destination_catalog()


@app.get("/v1/runs/{run_id}/deliveries")
async def run_deliveries(run_id: UUID) -> dict[str, Any]:
    try:
        return {"items": await service.deliveries(run_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@app.post("/v1/runs/{run_id}/deliveries", status_code=202)
async def create_delivery(run_id: UUID, request: CreateDeliveryRequest) -> dict[str, Any]:
    try:
        record = await service.create_delivery(
            run_id=run_id,
            report_id=request.report_id,
            connector_id=request.connector_id,
            destination_id=request.destination_id,
            mode=request.mode,
        )
        return await service.delivery_view(record)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run or report not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/deliveries/{delivery_id}/approve")
async def approve_delivery(delivery_id: UUID) -> dict[str, Any]:
    try:
        return await service.approve_delivery(delivery_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="delivery not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/deliveries/{delivery_id}/retry")
async def retry_delivery(delivery_id: UUID) -> dict[str, Any]:
    try:
        return await service.retry_delivery(delivery_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="delivery not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/artifacts/{artifact_id}")
def artifact(artifact_id: UUID) -> Response:
    try:
        record, content = artifacts.get(artifact_id)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    suffix = ".json" if record.mime_type == "application/json" else ".bin"
    return Response(
        content=content,
        media_type=record.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact_id}{suffix}"'},
    )


@app.post("/internal/worker/tasks/lease", dependencies=[Depends(authorize_worker)])
async def lease_task(request: WorkerLeaseRequest) -> Response:
    try:
        pool_filter = PartitionFilter(
            agent_types=frozenset(AgentType(value) for value in request.agent_types),
            partition_prefixes=request.partition_prefixes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    task = await service.lease_task(
        request.worker,
        tuple(str(value) for value in pool_filter.agent_types),
        pool_filter.partition_prefixes,
    )
    if task is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(task.model_dump(mode="json"))


@app.get("/internal/worker/tasks/{task_id}/context", dependencies=[Depends(authorize_worker)])
async def task_context(task_id: UUID) -> dict[str, Any]:
    try:
        return await service.context_for_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


@app.post("/internal/worker/tasks/{task_id}/result", dependencies=[Depends(authorize_worker)])
async def commit_task_result(task_id: UUID, request: WorkerCommitRequest) -> dict[str, Any]:
    if task_id != request.result.task_id or task_id != request.event.correlation_id:
        raise HTTPException(status_code=409, detail="task correlation mismatch")
    try:
        decision = await service.commit_result(request.result, request.event)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    return {
        "status": "committed",
        "gate": decision.model_dump(mode="json") if decision else None,
    }


@app.post("/internal/worker/artifacts", dependencies=[Depends(authorize_worker)])
async def put_worker_artifact(request: WorkerArtifactRequest) -> dict[str, Any]:
    record = artifacts.put(
        kind=request.kind,
        content=json.dumps(request.payload, sort_keys=True).encode(),
        mime_type="application/json",
        redact=True,
    )
    return {
        "artifact_id": str(record.id),
        "uri": record.uri,
        "sha256": record.sha256,
        "size": record.size,
        "redaction_status": record.redaction_status,
    }
