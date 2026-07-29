from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from apps.qa_worker.tools import build_tools
from sentinelqa.cli.main import main as cli_main
from sentinelqa.config import load_yaml


async def test_non_atlas_http_invariants_execute_with_persona_authentication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_authorization = ""

    def target_handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_authorization
        observed_authorization = request.headers.get("authorization", "")
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/v1/tasks":
            return httpx.Response(200, json={"items": [{"id": "task-1"}]})
        raise AssertionError(f"unexpected target request: {request.method} {request.url}")

    def control_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["kind"] == "api_exchange"
        return httpx.Response(200, json={"uri": "artifact://portable-exchange"})

    project = tmp_path / "sentinelqa-project"
    assert cli_main(["init", str(project)]) == 0
    monkeypatch.setenv("SENTINEL_APPLICATION_DIRECTORY", str(project / "applications"))
    monkeypatch.setenv("EXAMPLE_VIEWER_TOKEN", "private-viewer-token")
    target = httpx.AsyncClient(transport=httpx.MockTransport(target_handler))
    control = httpx.AsyncClient(
        base_url="http://control.test", transport=httpx.MockTransport(control_handler)
    )
    tools = build_tools(target, control)
    invariants = load_yaml(project / "invariants/example.yaml")["invariants"]

    ready = await tools["evaluate_invariant"](
        invariant=invariants[0], observations=[], entities=[], target_id="example-local"
    )
    tasks = await tools["evaluate_invariant"](
        invariant=invariants[1], observations=[], entities=[], target_id="example-local"
    )

    assert ready["passed"] is True
    assert tasks["passed"] is True
    assert observed_authorization == "Bearer private-viewer-token"
    await target.aclose()
    await control.aclose()
