from __future__ import annotations

from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator

from sentinelqa.agents.base import AgentExecutionContext, successful_result
from sentinelqa.domain.models import AgentResult, AgentTask
from sentinelqa.execution.schemathesis import response_schema, safe_operations
from sentinelqa.policy.tool_view import CapabilityScopedToolView


class APIExplorationAgent:
    agent_type = "api_explorer"
    version = "1.0"

    async def execute(
        self,
        task: AgentTask,
        context: AgentExecutionContext,
        tools: CapabilityScopedToolView,
    ) -> AgentResult:
        await context.budget.consume("tool_calls")
        target_id = str(context.input_data.get("target_id", "atlas-local"))
        persona = str(context.input_data.get("persona", "viewer"))
        document: dict[str, Any] = await tools.call("read_openapi", target_id=target_id)
        all_operations = [
            (method.upper(), path, operation)
            for path, path_item in sorted(document.get("paths", {}).items())
            if isinstance(path_item, dict)
            for method, operation in sorted(path_item.items())
            if method.lower() in {"get", "post", "put", "patch", "delete"}
            and isinstance(operation, dict)
        ]
        features = list(context.input_data.get("charter", {}).get("impacts", []))
        impacted_features = [str(item.get("feature", "")) for item in features]
        safe = safe_operations(document, features=impacted_features)
        responses: list[dict[str, Any]] = []
        schema_violations: list[dict[str, Any]] = []
        for method, path, operation in safe[: min(5, task.budget.max_api_requests)]:
            if context.cancellation_requested():
                break
            await context.budget.consume("tool_calls")
            await context.budget.consume("api_requests")
            response = await tools.call(
                "safe_api_request",
                method=method,
                path=path,
                operation_id=operation.get("operationId"),
                target_id=target_id,
                persona=persona,
            )
            response = {**response, "method": method, "path": path}
            responses.append(response)
            schema = response_schema(operation, int(response.get("status", 0)))
            if schema is not None:
                errors = list(Draft202012Validator(schema).iter_errors(response.get("body")))
                schema_violations.extend(
                    {
                        "method": method,
                        "path": path,
                        "message": error.message,
                    }
                    for error in errors[:10]
                )
        failures = [item for item in responses if int(item.get("status", 0)) >= 500]
        anomalies = [
            {
                "anomaly_id": str(uuid4()),
                "oracle_type": "explicit",
                "route": str(item["path"]),
                "action_type": "api_request",
                "signal": "http_5xx",
                "expected": "safe API request returns a non-5xx response",
                "actual": f"{item['method']} {item['path']} returned {item['status']}",
                "severity_hint": "high",
                "evidence_refs": [str(item["evidence_ref"])] if item.get("evidence_ref") else [],
                "suspected_component": "target.api",
                "replay_steps": [
                    {
                        "type": "api_request",
                        "method": item["method"],
                        "path": item["path"],
                        "target_id": target_id,
                        "expected_signal": "http_5xx",
                    }
                ],
            }
            for item in failures
        ]
        anomalies.extend(
            {
                "anomaly_id": str(uuid4()),
                "oracle_type": "schema",
                "route": str(item["path"]),
                "action_type": "api_contract",
                "signal": "response_schema_violation",
                "expected": "response validates against its declared OpenAPI schema",
                "actual": str(item["message"]),
                "severity_hint": "medium",
                "evidence_refs": [],
                "suspected_component": "target.api.contract",
                "replay_steps": [
                    {
                        "type": "api_request",
                        "method": item["method"],
                        "path": item["path"],
                        "target_id": target_id,
                        "expected_signal": "response_schema_violation",
                    }
                ],
            }
            for item in schema_violations
        )
        return successful_result(
            task,
            {
                "operations_discovered": len(all_operations),
                "operations_executed": len(responses),
                "server_errors": len(failures),
                "schema_violations": schema_violations,
                "anomalies": anomalies,
                "anomaly_candidate_ids": [item["anomaly_id"] for item in anomalies],
            },
            tuple(str(item["evidence_ref"]) for item in responses if item.get("evidence_ref")),
        )
