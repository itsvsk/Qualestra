from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from .state import FAULTS, AtlasState

app = FastAPI(title="Atlas reference SaaS", version="1.0.0")
state = AtlasState()
state.enable_profile(os.getenv("ATLAS_FAULT_PROFILE", "fixed"))


class WorkItemCreate(BaseModel):
    project_id: str
    title: str = Field(min_length=1, max_length=200)


class WorkItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None


class RoleChange(BaseModel):
    role: str


class PlanUpdate(BaseModel):
    project_limit: int = Field(ge=1, le=1000)


def actor(x_atlas_persona: Annotated[str, Header()] = "viewer") -> dict[str, Any]:
    try:
        return cast(dict[str, Any], state.data["users"][x_atlas_persona])
    except KeyError as exc:
        raise HTTPException(status_code=401, detail="unknown test persona") from exc


def test_token(x_test_control_token: Annotated[str | None, Header()] = None) -> None:
    if os.getenv("ATLAS_TEST_MODE", "false").lower() != "true":
        raise HTTPException(status_code=404)
    expected = os.getenv("ATLAS_TEST_CONTROL_TOKEN", "local-test-token")
    if not expected or x_test_control_token != expected:
        raise HTTPException(status_code=403, detail="invalid test control token")


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/__test/reset", dependencies=[Depends(test_token)])
def reset() -> dict[str, str]:
    state.reset()
    return {"status": "reset"}


@app.post("/__test/seed", dependencies=[Depends(test_token)])
def seed(seed: int = 41021) -> dict[str, Any]:
    state.seed(seed)
    return {"status": "seeded", "seed": seed}


@app.post("/__test/faults/{fault_id}/enable", dependencies=[Depends(test_token)])
def enable_fault(fault_id: str) -> dict[str, Any]:
    fault_id = fault_id.upper()
    if fault_id not in FAULTS:
        raise HTTPException(status_code=404, detail="unknown fault")
    state.faults.add(fault_id)
    return {"enabled": sorted(state.faults)}


@app.post("/__test/faults/{fault_id}/disable", dependencies=[Depends(test_token)])
def disable_fault(fault_id: str) -> dict[str, Any]:
    state.faults.discard(fault_id.upper())
    return {"enabled": sorted(state.faults)}


@app.get("/__test/state", dependencies=[Depends(test_token)])
def test_state() -> dict[str, Any]:
    return state.snapshot()


@app.get("/v1/projects")
async def list_projects(user: Annotated[dict[str, Any], Depends(actor)]) -> list[dict[str, Any]]:
    projects = [
        item for item in state.data["projects"].values() if item["org_id"] == user["org_id"]
    ]
    if "F09" in state.faults and len(projects) > 0:
        await asyncio.sleep(0.35)
    return projects


@app.get("/v1/accessibility-snapshot")
def accessibility_snapshot() -> dict[str, Any]:
    violations = []
    if "F10" in state.faults:
        violations.extend(
            [
                {"id": "label", "impact": "serious", "help": "Form elements must have labels"},
                {
                    "id": "focus-order-semantics",
                    "impact": "serious",
                    "help": "Dialog focus is not restored",
                },
            ]
        )
    return {"violations": violations}


@app.get("/v1/work-items/{item_id}")
def get_work_item(item_id: str, user: Annotated[dict[str, Any], Depends(actor)]) -> dict[str, Any]:
    item = state.data["work_items"].get(item_id)
    if not item or (item["org_id"] != user["org_id"] and "F02" not in state.faults):
        raise HTTPException(status_code=404, detail="work item not found")
    return deepcopy(item)


@app.post("/v1/work-items", status_code=status.HTTP_201_CREATED)
def create_work_item(
    payload: WorkItemCreate,
    user: Annotated[dict[str, Any], Depends(actor)],
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="viewer is read-only")
    project = state.data["projects"].get(payload.project_id)
    if not project or project["org_id"] != user["org_id"]:
        raise HTTPException(status_code=404, detail="project not found")
    if x_idempotency_key:
        for item in state.data["work_items"].values():
            if item.get("idempotency_key") == x_idempotency_key:
                return deepcopy(item)
    item_id = state.next_id("work_item")
    item = {
        "id": item_id,
        "org_id": user["org_id"],
        "project_id": payload.project_id,
        "title": payload.title,
        "status": "open",
        "idempotency_key": x_idempotency_key,
    }
    state.data["work_items"][item_id] = item
    if "F04" in state.faults:
        duplicate_id = state.next_id("work_item")
        state.data["work_items"][duplicate_id] = {**item, "id": duplicate_id}
    state.audit(user["org_id"], user["id"], "work_item.created", item_id)
    return deepcopy(item)


@app.patch("/v1/work-items/{item_id}")
def update_work_item(
    item_id: str,
    payload: WorkItemUpdate,
    user: Annotated[dict[str, Any], Depends(actor)],
    response: Response,
) -> dict[str, Any]:
    item = state.data["work_items"].get(item_id)
    if not item or item["org_id"] != user["org_id"]:
        raise HTTPException(status_code=404, detail="work item not found")
    if user["role"] == "viewer" and "F01" not in state.faults:
        raise HTTPException(status_code=403, detail="viewer is read-only")
    update = payload.model_dump(exclude_none=True)
    if "F05" in state.faults and user["role"] != "viewer":
        raise HTTPException(status_code=500, detail="injected update failure")
    if "F06" not in state.faults:
        item.update(update)
    else:
        response.headers["x-atlas-transient-title"] = update.get("title", item["title"])
    state.audit(user["org_id"], user["id"], "work_item.updated", item_id)
    return {**deepcopy(item), **update}


@app.patch("/v1/organizations/{org_id}/plan-limit")
def update_plan_limit(
    org_id: str, payload: PlanUpdate, user: Annotated[dict[str, Any], Depends(actor)]
) -> dict[str, Any]:
    if user["role"] != "owner" or user["org_id"] != org_id:
        raise HTTPException(status_code=403)
    organization = state.data["organizations"][org_id]
    if "F03" not in state.faults:
        organization["project_limit"] = payload.project_limit
    state.audit(org_id, user["id"], "plan_limit.updated", org_id)
    return {**deepcopy(organization), "project_limit": payload.project_limit}


@app.patch("/v1/users/{user_id}/role")
def change_role(
    user_id: str, payload: RoleChange, user: Annotated[dict[str, Any], Depends(actor)]
) -> dict[str, Any]:
    if user["role"] not in {"owner", "admin"}:
        raise HTTPException(status_code=403)
    target = state.data["users"].get(user_id)
    if not target or target["org_id"] != user["org_id"]:
        raise HTTPException(status_code=404)
    target["role"] = payload.role
    if "F08" not in state.faults:
        state.audit(user["org_id"], user["id"], "member.role_changed", user_id)
    return deepcopy(target)


@app.post("/v1/exports", status_code=status.HTTP_202_ACCEPTED)
def start_export(user: Annotated[dict[str, Any], Depends(actor)]) -> dict[str, Any]:
    if user["role"] == "viewer":
        raise HTTPException(status_code=403)
    export_id = state.next_id("export")
    state.data["exports"][export_id] = {
        "id": export_id,
        "org_id": user["org_id"],
        "status": "completed",
        "size": 0 if "F07" in state.faults else 42,
    }
    return deepcopy(state.data["exports"][export_id])


@app.get("/v1/exports/{export_id}/download")
def download_export(export_id: str, user: Annotated[dict[str, Any], Depends(actor)]) -> Response:
    export = state.data["exports"].get(export_id)
    if not export or export["org_id"] != user["org_id"]:
        raise HTTPException(status_code=404)
    content = b"" if export["size"] == 0 else b"id,title\nitem-a,Prepare launch\n"
    return Response(content=content, media_type="text/csv")
