from __future__ import annotations

import os
from collections.abc import Mapping
from copy import deepcopy
from time import perf_counter
from typing import Any
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator

from sentinelqa.adapters import (
    AuthMaterial,
    EnvironmentSecretResolver,
    HttpInvariant,
    HttpInvariantRunner,
    ProjectAdapter,
    SafeHttpResponse,
    load_project_adapter_by_id,
)
from sentinelqa.adapters.auth import FormCredentials
from sentinelqa.domain.enums import SafetyClass
from sentinelqa.domain.models import CandidateAction
from sentinelqa.execution.schemathesis import response_schema
from sentinelqa.policy.engine import PolicyEngine
from sentinelqa.policy.tool_view import Tool

from .browser_tools import PlaywrightUITools


def build_tools(atlas: httpx.AsyncClient, control: httpx.AsyncClient) -> Mapping[str, Tool]:
    atlas_web_url = os.getenv("ATLAS_WEB_URL", "http://atlas-web")
    test_token = os.getenv("ATLAS_TEST_CONTROL_TOKEN", "local-test-token")
    test_headers = {"x-test-control-token": test_token}
    observations_by_control: dict[str, dict[str, Any]] = {}
    browser_tools = (
        PlaywrightUITools(atlas_web_url, os.getenv("ARTIFACT_ROOT", "/artifacts"))
        if os.getenv("SENTINEL_BROWSER_MODE", "heuristic") == "playwright"
        else None
    )
    application_directory = os.getenv("SENTINEL_APPLICATION_DIRECTORY", "config/applications")

    def project(target_id: str) -> ProjectAdapter:
        return load_project_adapter_by_id(target_id, application_directory)

    async def authentication(
        target_id: str, persona: str | None, client: httpx.AsyncClient
    ) -> AuthMaterial:
        if persona is None:
            return AuthMaterial()
        adapter = project(target_id)

        class FormTransport:
            async def form_login(self, credentials: FormCredentials) -> AuthMaterial:
                login_url = urljoin(
                    adapter.application.base_url.rstrip("/") + "/",
                    credentials.login_path.lstrip("/"),
                )
                response = await client.post(
                    login_url, data=credentials.fields, follow_redirects=False
                )
                if response.status_code not in credentials.expected_statuses:
                    raise PermissionError(
                        f"form login returned unexpected status {response.status_code}"
                    )
                return AuthMaterial(cookies=dict(response.cookies))

        material = await adapter.authentication_for(persona).prepare(
            FormTransport(), EnvironmentSecretResolver()
        )
        if target_id == "atlas-local":
            return AuthMaterial(
                headers={"x-atlas-persona": persona, **material.headers},
                cookies=material.cookies,
            )
        return material

    async def read_openapi(*, target_id: str = "atlas-local") -> dict[str, Any]:
        application = project(target_id).application
        if application.openapi_url is None:
            return {
                "openapi": "3.1.0",
                "info": {"title": target_id, "version": "0"},
                "paths": {},
            }
        response = await atlas.get(application.openapi_url)
        response.raise_for_status()
        return dict(response.json())

    async def safe_api_request(
        *,
        method: str,
        path: str,
        operation_id: str | None = None,
        target_id: str = "atlas-local",
        persona: str | None = "viewer",
        json_body: Any = None,
    ) -> dict[str, Any]:
        del operation_id
        replacements = {
            "{item_id}": "item-a",
            "{export_id}": "export-1",
            "{org_id}": "org-a",
            "{user_id}": "editor",
            "{fault_id}": "F01",
        }
        for marker, value in replacements.items():
            path = path.replace(marker, value)
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise ValueError("API requests must use a target-relative path")
        application = project(target_id).application
        url = urljoin(application.api_base_url.rstrip("/") + "/", path.lstrip("/"))
        parsed_url = urlsplit(url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        safety = (
            SafetyClass.SAFE
            if method.upper() in {"GET", "HEAD", "OPTIONS"}
            else SafetyClass.CONTROLLED
        )
        PolicyEngine(set(application.allowed_origins)).require_authorized(
            CandidateAction(
                action_type="api_request",
                input=f"{method.upper()} {path}",
                safety_class=safety,
                target_origin=origin,
            )
        )
        material = await authentication(target_id, persona, atlas)
        headers = dict(material.headers)
        if target_id == "atlas-local":
            headers.update(test_headers)
        response = await atlas.request(
            method,
            url,
            headers=headers,
            cookies=material.cookies or None,
            json=json_body,
        )
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text[:500]
        evidence_ref = await record_evidence(
            "api_exchange",
            {
                "method": method,
                "path": path,
                "target_id": target_id,
                "status": response.status_code,
                "body": body,
            },
        )
        return {"status": response.status_code, "body": body, "evidence_ref": evidence_ref}

    async def observe_ui(*, context: dict[str, Any]) -> dict[str, Any]:
        if browser_tools is not None:
            return await browser_tools.observe(context)
        persona = str(context.get("persona", "viewer"))
        base_url = str(context.get("base_url", atlas_web_url)).rstrip("/")
        parsed = urlsplit(base_url)
        observation: dict[str, Any] = {
            "schema_version": "1.0",
            "run_id": str(context.get("run_id", uuid4())),
            "session_id": str(uuid4()),
            "state_fingerprint": f"atlas-home-{persona}",
            "page": {
                "url": base_url,
                "route": parsed.path or "/",
                "title": "Atlas",
                "landmarks": ["main", "navigation"],
            },
            "persona": {"id": persona, "role": persona},
            "environment": {
                "browser": str(context.get("browser", "heuristic")),
                **dict(context.get("browser_settings", {})),
            },
            "controls": [
                {
                    "id": "projects-link",
                    "type": "link",
                    "name": "Open projects",
                    "enabled": True,
                    "visible": True,
                    "safety_hint": "safe",
                }
            ],
            "signals": {"accessibility_violations": []},
            "evidence": {},
            "entities": [],
        }
        for item in observation["controls"]:
            observations_by_control[str(item["id"])] = deepcopy(observation)
        return observation

    async def safe_ui_action(*, candidate: dict[str, Any]) -> dict[str, Any]:
        if browser_tools is not None:
            return await browser_tools.execute(candidate)
        control_id = str(candidate.get("control_id", ""))
        before = deepcopy(observations_by_control[control_id])
        after = deepcopy(before)
        after["state_fingerprint"] = f"{before['state_fingerprint']}:projects"
        after["page"] = {
            **before["page"],
            "url": f"{before['page']['url'].rstrip('/')}/projects",
            "route": "/projects",
            "title": "Projects | Atlas",
        }
        return {
            "executed": True,
            "candidate": candidate,
            "policy_violation": False,
            "before": before,
            "after": after,
            "outcome": "succeeded",
            "duration_ms": 1,
        }

    async def reset_test_target(*, target_id: str = "atlas-local") -> dict[str, Any]:
        application = project(target_id).application
        if application.test_control_url is None:
            return {"status": "skipped", "reason": "no_test_control"}
        response = await atlas.post(
            f"{application.test_control_url.rstrip('/')}/reset", headers=test_headers
        )
        response.raise_for_status()
        return dict(response.json())

    async def seed_test_target(*, seed: int, target_id: str = "atlas-local") -> dict[str, Any]:
        application = project(target_id).application
        if application.test_control_url is None:
            return {"status": "skipped", "reason": "no_test_control", "seed": seed}
        response = await atlas.post(
            f"{application.test_control_url.rstrip('/')}/seed",
            params={"seed": seed},
            headers=test_headers,
        )
        response.raise_for_status()
        return dict(response.json())

    async def snapshot() -> dict[str, Any]:
        response = await atlas.get("/__test/state", headers=test_headers)
        response.raise_for_status()
        return dict(response.json())

    async def record_evidence(kind: str, payload: dict[str, Any]) -> str:
        response = await control.post(
            "/internal/worker/artifacts", json={"kind": kind, "payload": payload}
        )
        response.raise_for_status()
        return str(response.json()["uri"])

    async def probe_invariant(invariant: dict[str, Any]) -> dict[str, Any]:
        invariant_id = str(invariant.get("id", "unknown"))
        seed = int(invariant.get("seed", 41021))
        await reset_test_target()
        await seed_test_target(seed=seed)
        route = "/"
        signal = ""
        expected = "invariant holds"
        actual = "invariant holds"
        oracle_type = "invariant"
        component = "atlas-api"
        evidence: dict[str, Any] = {"invariant_id": invariant_id, "seed": seed}

        if invariant_id == "viewer_no_mutation":
            route = "/v1/work-items/{item_id}"
            response = await atlas.patch(
                "/v1/work-items/item-a",
                headers={"x-atlas-persona": "viewer"},
                json={"title": "Viewer mutation probe"},
            )
            evidence["response_status"] = response.status_code
            if response.status_code != 403:
                signal, expected, actual = (
                    "authorization_bypass",
                    "viewer mutation returns 403",
                    f"viewer mutation returned {response.status_code}",
                )
        elif invariant_id == "tenant_isolation":
            route = "/v1/work-items/{item_id}"
            response = await atlas.get(
                "/v1/work-items/item-b", headers={"x-atlas-persona": "viewer"}
            )
            evidence["response_status"] = response.status_code
            if response.status_code != 404:
                signal, expected, actual = (
                    "tenant_isolation_bypass",
                    "cross-tenant resource returns 404",
                    f"cross-tenant resource returned {response.status_code}",
                )
        elif invariant_id == "plan_limit_enforced":
            route = "/v1/organizations/{org_id}/plan-limit"
            response = await atlas.patch(
                "/v1/organizations/org-a/plan-limit",
                headers={"x-atlas-persona": "owner"},
                json={"project_limit": 9},
            )
            persisted = (await snapshot())["organizations"]["org-a"]["project_limit"]
            returned = response.json().get("project_limit")
            evidence.update(
                {
                    "response_status": response.status_code,
                    "returned": returned,
                    "persisted": persisted,
                }
            )
            if response.status_code == 200 and returned != persisted:
                signal, expected, actual, oracle_type = (
                    "plan_limit_differential",
                    "returned plan limit equals persisted state",
                    f"returned {returned}; persisted {persisted}",
                    "differential",
                )
        elif invariant_id == "create_visible_ui_api":
            route = "/v1/work-items"
            before = len((await snapshot())["work_items"])
            response = await atlas.post(
                "/v1/work-items",
                headers={
                    "x-atlas-persona": "editor",
                    "x-idempotency-key": "invariant-create",
                },
                json={"project_id": "project-a", "title": "Cardinality probe"},
            )
            after = len((await snapshot())["work_items"])
            evidence.update(
                {"response_status": response.status_code, "cardinality_delta": after - before}
            )
            if response.status_code == 201 and after - before != 1:
                signal, expected, actual, oracle_type = (
                    "duplicate_creation",
                    "one create increases cardinality by one",
                    f"cardinality increased by {after - before}",
                    "metamorphic",
                )
        elif invariant_id == "update_survives_refresh":
            route = "/v1/work-items/{item_id}"
            response = await atlas.patch(
                "/v1/work-items/item-a",
                headers={"x-atlas-persona": "editor"},
                json={"title": "Persisted title"},
            )
            evidence["response_status"] = response.status_code
            if response.status_code >= 500:
                signal, expected, actual, oracle_type = (
                    "http_5xx",
                    "update returns a non-server-error response",
                    f"update returned {response.status_code}",
                    "explicit_network",
                )
            elif response.status_code == 200:
                refreshed = await atlas.get(
                    "/v1/work-items/item-a", headers={"x-atlas-persona": "editor"}
                )
                returned = response.json()["title"]
                persisted = refreshed.json()["title"]
                evidence.update({"returned": returned, "refreshed": persisted})
                if returned != persisted:
                    signal, expected, actual, oracle_type = (
                        "refresh_persistence",
                        "updated value survives a clean retrieve",
                        f"returned {returned!r}; refreshed {persisted!r}",
                        "metamorphic",
                    )
        elif invariant_id == "completed_export_nonempty":
            route = "/v1/exports/{export_id}/download"
            created = await atlas.post("/v1/exports", headers={"x-atlas-persona": "admin"})
            export = created.json()
            downloaded = await atlas.get(
                f"/v1/exports/{export['id']}/download",
                headers={"x-atlas-persona": "admin"},
            )
            evidence.update({"export": export, "download_bytes": len(downloaded.content)})
            if export.get("status") == "completed" and not downloaded.content:
                signal, expected, actual = (
                    "empty_completed_export",
                    "completed export contains data",
                    "completed export contained zero bytes",
                )
        elif invariant_id == "sensitive_action_audited":
            route = "/v1/users/{user_id}/role"
            before = len((await snapshot())["audit_events"])
            response = await atlas.patch(
                "/v1/users/editor/role",
                headers={"x-atlas-persona": "admin"},
                json={"role": "viewer"},
            )
            after = len((await snapshot())["audit_events"])
            evidence.update(
                {"response_status": response.status_code, "audit_delta": after - before}
            )
            if response.status_code == 200 and after == before:
                signal, expected, actual = (
                    "missing_audit_event",
                    "role change emits an audit event",
                    "role change emitted no audit event",
                )
        elif invariant_id == "project_list_latency":
            route = "/v1/projects"
            started = perf_counter()
            await atlas.get("/v1/projects", headers={"x-atlas-persona": "editor"})
            duration = perf_counter() - started
            evidence["duration_seconds"] = duration
            if duration > 0.25:
                signal, expected, actual, oracle_type = (
                    "latency_threshold",
                    "project list completes within 0.25 seconds",
                    f"project list took {duration:.3f} seconds",
                    "anomaly",
                )
        elif invariant_id == "accessibility_serious":
            route = "/v1/accessibility-snapshot"
            response = await atlas.get(route)
            violations = [
                item
                for item in response.json()["violations"]
                if item.get("impact") in {"critical", "serious"}
            ]
            evidence["violations"] = violations
            if violations:
                signal, expected, actual, oracle_type, component = (
                    "accessibility_serious",
                    "no critical or serious accessibility violations",
                    f"found {len(violations)} serious violations",
                    "accessibility",
                    "atlas-web",
                )

        if not signal:
            return {"invariant_id": invariant_id, "passed": True}
        evidence_ref = await record_evidence("invariant_probe", evidence)
        return {
            "invariant_id": invariant_id,
            "passed": False,
            "anomaly_id": str(uuid4()),
            "oracle_type": oracle_type,
            "route": route,
            "action_type": "api_request",
            "signal": signal,
            "expected": expected,
            "actual": actual,
            "severity_hint": str(invariant.get("severity", "medium")),
            "evidence_ref": evidence_ref,
            "evidence_refs": [evidence_ref],
            "suspected_component": component,
            "replay_steps": [
                {
                    "type": "invariant_probe",
                    "invariant": invariant,
                    "expected_signal": signal,
                }
            ],
        }

    async def evaluate_invariant(
        *,
        invariant: dict[str, Any],
        observations: list[Any],
        entities: list[Any],
        target_id: str = "atlas-local",
    ) -> dict[str, Any]:
        del observations, entities
        if "request" in invariant:
            rule = HttpInvariant.model_validate(
                {key: value for key, value in invariant.items() if key != "seed"}
            )

            class RuntimeHttpTool:
                async def request(
                    self,
                    method: str,
                    path: str,
                    *,
                    json_body: Any,
                    authentication: AuthMaterial,
                ) -> SafeHttpResponse:
                    del authentication
                    started = perf_counter()
                    response = await safe_api_request(
                        method=method,
                        path=path,
                        target_id=target_id,
                        persona=rule.persona,
                        json_body=json_body,
                    )
                    return SafeHttpResponse(
                        status=int(response["status"]),
                        duration_ms=int((perf_counter() - started) * 1000),
                        json_body=response["body"],
                        evidence_ref=str(response["evidence_ref"]),
                    )

            result = await HttpInvariantRunner(RuntimeHttpTool()).execute(rule)
            if result.passed:
                return {
                    "invariant_id": rule.id,
                    "passed": True,
                    "evidence_ref": result.evidence_refs[0],
                }
            failed = next(check for check in result.checks if not check.passed)
            return {
                "invariant_id": rule.id,
                "passed": False,
                "anomaly_id": str(uuid4()),
                "oracle_type": "invariant",
                "route": rule.request.path,
                "action_type": "api_request",
                "signal": failed.name,
                "expected": str(failed.expected),
                "actual": str(failed.actual),
                "severity_hint": rule.severity,
                "evidence_ref": result.evidence_refs[0],
                "evidence_refs": list(result.evidence_refs),
                "suspected_component": f"{target_id}.api",
                "replay_steps": [
                    {
                        "type": "http_invariant",
                        "target_id": target_id,
                        "invariant": rule.model_dump(mode="json", by_alias=True),
                        "expected_signal": failed.name,
                    }
                ],
            }
        return await probe_invariant(invariant)

    async def replay_ui(
        *, steps: list[dict[str, Any]], target_id: str = "atlas-local"
    ) -> dict[str, Any]:
        del target_id
        outcomes: list[dict[str, Any]] = []
        for step in steps:
            if step.get("type") == "invariant_probe" and "invariant" in step:
                outcomes.append(await probe_invariant(dict(step["invariant"])))
            elif step.get("type") == "accessibility_scan" and "context" in step:
                observation = await observe_ui(context=dict(step["context"]))
                signals = {
                    str(item.get("id"))
                    for item in observation.get("signals", {}).get("accessibility_violations", [])
                }
                expected_signal = str(step.get("expected_signal"))
                outcomes.append(
                    {
                        "passed": expected_signal not in signals,
                        "signal": expected_signal,
                        "evidence_refs": [
                            ref for ref in observation.get("evidence", {}).values() if ref
                        ],
                    }
                )
        matched = bool(outcomes) and all(not item.get("passed", True) for item in outcomes)
        return {
            "matched": matched,
            "instability": 0,
            "evidence_refs": [ref for item in outcomes for ref in item.get("evidence_refs", [])],
        }

    async def replay_api(
        *, steps: list[dict[str, Any]], target_id: str = "atlas-local"
    ) -> dict[str, Any]:
        outcomes: list[dict[str, Any]] = []
        document: dict[str, Any] | None = None
        for step in steps:
            if step.get("type") == "http_invariant":
                result = await evaluate_invariant(
                    invariant=dict(step["invariant"]),
                    observations=[],
                    entities=[],
                    target_id=str(step.get("target_id", target_id)),
                )
                outcomes.append(
                    {
                        "matched": not bool(result["passed"]),
                        "evidence_ref": result.get("evidence_ref"),
                    }
                )
                continue
            if step.get("type") != "api_request":
                continue
            method = str(step.get("method", "GET")).upper()
            path = str(step["path"])
            request_target = str(step.get("target_id", target_id))
            response = await safe_api_request(method=method, path=path, target_id=request_target)
            signal = str(step.get("expected_signal"))
            matched = signal == "http_5xx" and int(response["status"]) >= 500
            if signal == "response_schema_violation":
                document = document or await read_openapi(target_id=request_target)
                operation = document.get("paths", {}).get(path, {}).get(method.lower(), {})
                schema = response_schema(operation, int(response["status"]))
                matched = schema is not None and bool(
                    list(Draft202012Validator(schema).iter_errors(response["body"]))
                )
            outcomes.append({"matched": matched, "evidence_ref": response["evidence_ref"]})
        return {
            "matched": bool(outcomes) and all(item["matched"] for item in outcomes),
            "instability": 0,
            "evidence_refs": [
                item["evidence_ref"] for item in outcomes if item.get("evidence_ref")
            ],
        }

    return {
        "read_openapi": read_openapi,
        "safe_api_request": safe_api_request,
        "controlled_api_request": safe_api_request,
        "observe_ui": observe_ui,
        "safe_ui_action": safe_ui_action,
        "controlled_ui_action": safe_ui_action,
        "evaluate_invariant": evaluate_invariant,
        "reset_test_target": reset_test_target,
        "seed_test_target": seed_test_target,
        "replay_ui": replay_ui,
        "replay_api": replay_api,
    }
