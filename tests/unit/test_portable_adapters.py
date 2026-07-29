from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID

import httpx
import pytest
from pydantic import JsonValue, ValidationError

from sentinelqa.adapters import (
    ApiTokenAuthConfig,
    ApplicationConfig,
    AuthMaterial,
    BearerAuthConfig,
    EnvironmentSecretResolver,
    FormAuthConfig,
    HttpInvariant,
    HttpInvariantRunner,
    InvariantDocument,
    OIDCTokenAuthConfig,
    PolicySafeHttpTool,
    ProjectAdapterRegistry,
    SafeHttpResponse,
    build_auth_adapter,
    load_application,
    load_invariants,
    load_personas,
    load_project_adapter,
)
from sentinelqa.adapters.auth import FormCredentials
from sentinelqa.policy.engine import PolicyEngine
from sentinelqa.storage.artifacts import LocalArtifactStore


class FakeTransport:
    def __init__(self) -> None:
        self.credentials: FormCredentials | None = None

    async def form_login(self, credentials: FormCredentials) -> AuthMaterial:
        self.credentials = credentials
        return AuthMaterial(cookies={"session": "private-session"})


class FakeHttpTool:
    def __init__(self, response: SafeHttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, AuthMaterial]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: JsonValue | None,
        authentication: AuthMaterial,
    ) -> SafeHttpResponse:
        del json_body
        self.calls.append((method, path, authentication))
        return self.response


def test_loads_legacy_atlas_and_portable_non_atlas_project() -> None:
    atlas = load_application("config/applications/atlas.yaml")
    assert atlas.id == "atlas-local"
    assert len(load_personas("config/personas.yaml")) == 5

    adapter = load_project_adapter("config/applications/acme-tasks.yaml")
    assert adapter.application.id == "acme-tasks-local"
    assert set(adapter.personas()) == {"owner", "viewer"}
    assert type(adapter.authentication_for("viewer")).__name__ == "_HeaderAuthAdapter"

    document = load_invariants("config/invariants/acme-tasks.yaml")
    assert [item.id for item in document.invariants] == [
        "service_is_ready",
        "viewer_reads_tasks",
    ]


def test_application_rejects_unallowlisted_endpoint_and_unknown_adapter() -> None:
    with pytest.raises(ValidationError, match="api_base_url origin is not allowlisted"):
        ApplicationConfig.model_validate(
            {
                "id": "bad",
                "base_url": "https://app.example",
                "api_base_url": "https://evil.example",
                "allowed_origins": ["https://app.example"],
            }
        )
    app = ApplicationConfig.model_validate(
        {
            "id": "custom",
            "adapter": "runtime-import-is-forbidden",
            "base_url": "https://app.example",
            "api_base_url": "https://app.example",
            "allowed_origins": ["https://app.example"],
        }
    )
    with pytest.raises(ValueError, match="not registered"):
        ProjectAdapterRegistry().create(app)


@pytest.mark.parametrize(
    "path", ["https://evil.example/data", "//evil.example/data", "/safe/../secret", "/v1/{id}"]
)
def test_invariant_rejects_unsafe_or_dynamic_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        HttpInvariant.model_validate(
            {
                "id": "safe_path_check",
                "description": "A safe path is required.",
                "request": {"path": path},
                "expect": {"status": 200},
            }
        )


async def test_authentication_adapters_resolve_secrets_only_at_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_TOKEN", "private-token")
    monkeypatch.setenv("TEST_USER", "private-user")
    monkeypatch.setenv("TEST_PASSWORD", "private-password")
    resolver = EnvironmentSecretResolver()
    transport = FakeTransport()

    bearer = await build_auth_adapter(
        BearerAuthConfig(token_ref="env:TEST_TOKEN")  # noqa: S106 - opaque reference
    ).prepare(transport, resolver)
    assert bearer.headers == {"Authorization": "Bearer private-token"}
    assert "private-token" not in repr(bearer)

    oidc = await build_auth_adapter(
        OIDCTokenAuthConfig(
            access_token_ref="env:TEST_TOKEN"  # noqa: S106 - opaque reference
        )
    ).prepare(transport, resolver)
    assert oidc.headers["Authorization"] == "Bearer private-token"

    api_token = await build_auth_adapter(
        ApiTokenAuthConfig(token_ref="env:TEST_TOKEN")  # noqa: S106 - opaque reference
    ).prepare(transport, resolver)
    assert api_token.headers == {"X-API-Key": "private-token"}

    form = await build_auth_adapter(
        FormAuthConfig(
            login_path="/session",
            username_ref="env:TEST_USER",
            password_ref="env:TEST_PASSWORD",  # noqa: S106 - opaque reference
        )
    ).prepare(transport, resolver)
    assert form.cookies == {"session": "private-session"}
    assert transport.credentials is not None
    assert transport.credentials.fields["password"] == "private-password"  # noqa: S105
    assert "private-password" not in repr(transport.credentials)

    with pytest.raises(ValidationError, match="opaque env:NAME"):
        BearerAuthConfig(token_ref="literal-secret")  # noqa: S106


async def test_invariant_runner_evaluates_status_json_latency_and_evidence() -> None:
    tool = FakeHttpTool(
        SafeHttpResponse(
            status=200,
            duration_ms=42,
            json_body=cast(JsonValue, {"status": "ready", "items": [1, 2]}),
            evidence_ref="artifact://response/1",
        )
    )
    invariant = HttpInvariant.model_validate(
        {
            "id": "service_ready",
            "description": "Service must be ready.",
            "request": {"method": "GET", "path": "/health"},
            "expect": {
                "status": [200, 204],
                "max_duration_ms": 100,
                "json": [
                    {"path": "$.status", "operator": "equals", "value": "ready"},
                    {"path": "$.items", "operator": "length_equals", "value": 2},
                ],
            },
        }
    )

    result = await HttpInvariantRunner(tool).execute(invariant)
    assert result.passed
    assert result.evidence_refs == ("artifact://response/1",)
    assert len(result.checks) == 4
    assert tool.calls[0][0:2] == ("GET", "/health")


async def test_mutating_invariants_require_explicit_runtime_authorization() -> None:
    tool = FakeHttpTool(SafeHttpResponse(204, 1, None, "artifact://response/2"))
    invariant = HttpInvariant.model_validate(
        {
            "id": "update_persists",
            "description": "An authorized update persists.",
            "request": {"method": "PATCH", "path": "/v1/profile", "json_body": {"x": 1}},
            "expect": {"status": 204},
        }
    )
    with pytest.raises(PermissionError, match="explicit runtime authorization"):
        await HttpInvariantRunner(tool).execute(invariant)
    assert (await HttpInvariantRunner(tool, allow_mutations=True).execute(invariant)).passed


def test_config_models_publish_machine_readable_json_schemas() -> None:
    assert "allowed_origins" in ApplicationConfig.model_json_schema()["properties"]
    schema = InvariantDocument.model_json_schema()
    assert schema["properties"]["version"]["const"] == 1


async def test_concrete_http_tool_enforces_policy_and_redacts_evidence(tmp_path: Path) -> None:
    observed_authorization = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_authorization
        observed_authorization = request.headers["authorization"]
        return httpx.Response(200, json={"status": "ready"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    policy = PolicyEngine({"https://api.example.test"})
    tool = PolicySafeHttpTool(
        base_url="https://api.example.test",
        policy=policy,
        artifacts=artifacts,
        client=client,
    )
    response = await tool.request(
        "GET",
        "/health",
        json_body=None,
        authentication=AuthMaterial(headers={"Authorization": "Bearer private-token"}),
    )
    assert response.status == 200
    assert observed_authorization == "Bearer private-token"
    artifact_id = UUID(response.evidence_ref.removeprefix("artifact://"))
    _, evidence = artifacts.get(artifact_id)
    assert b"private-token" not in evidence
    assert b"REDACTED" in evidence
    await client.aclose()

    with pytest.raises(ValueError, match="not policy-allowlisted"):
        PolicySafeHttpTool(
            base_url="https://outside.example.test",
            policy=policy,
            artifacts=artifacts,
        )
