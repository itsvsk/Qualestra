from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from playwright.async_api import async_playwright

from sentinelqa.adapters import AuthMaterial, EnvironmentSecretResolver, load_project_adapter_by_id
from sentinelqa.adapters.auth import FormCredentials
from sentinelqa.domain.models import CandidateAction, EvidenceRefs, PersonaRef
from sentinelqa.execution.browser import BrowserExecutor, PlaywrightObserver
from sentinelqa.storage import build_artifact_store_from_env

ROLE_BY_PERSONA = {
    "owner": "owner",
    "admin": "admin",
    "editor": "editor",
    "viewer": "viewer",
    "new_user": "editor",
    "other_owner": "owner",
}


@dataclass(slots=True)
class BrowserSession:
    context: Any
    observer: PlaywrightObserver
    executor: BrowserExecutor


class PlaywrightUITools:
    def __init__(self, base_url: str, artifact_root: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.artifacts = build_artifact_store_from_env(
            {**dict(os.environ), "ARTIFACT_ROOT": artifact_root}
        )
        self._playwright: Any = None
        self._browsers: dict[str, Any] = {}
        self._sessions: dict[str, BrowserSession] = {}
        self._control_sessions: dict[str, str] = {}
        self._startup_lock = asyncio.Lock()

    async def _ensure_browser(self, browser_name: str) -> Any:
        if browser_name not in {"chromium", "firefox", "webkit"}:
            raise ValueError(f"unsupported browser: {browser_name}")
        async with self._startup_lock:
            if browser_name in self._browsers:
                return self._browsers[browser_name]
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            browser = await getattr(self._playwright, browser_name).launch(headless=True)
            self._browsers[browser_name] = browser
            return browser

    async def observe(self, context: dict[str, Any]) -> dict[str, Any]:
        run_id = UUID(str(context["run_id"]))
        persona_id = str(context.get("persona", "viewer"))
        role = ROLE_BY_PERSONA.get(persona_id, "viewer")
        browser_name = str(context.get("browser", "chromium"))
        browser_settings = dict(context.get("browser_settings", {}))
        target_id = str(context.get("target_id", "atlas-local"))
        base_url = str(context.get("base_url", self.base_url)).rstrip("/")
        allowed_origins = set(context.get("allowed_origins", []))
        session_key = (
            f"{run_id}:{target_id}:{persona_id}:{browser_name}:{base_url}:{browser_settings}"
        )
        session = self._sessions.get(session_key)
        if session is None:
            browser = await self._ensure_browser(browser_name)
            browser_context = await browser.new_context(
                viewport={
                    "width": int(browser_settings.get("viewport_width", 1440)),
                    "height": int(browser_settings.get("viewport_height", 900)),
                },
                locale=str(browser_settings.get("locale", "en-US")),
                timezone_id=str(browser_settings.get("timezone_id", "UTC")),
                color_scheme=str(browser_settings.get("color_scheme", "light")),
                reduced_motion=str(browser_settings.get("reduced_motion", "no-preference")),
            )
            adapter = load_project_adapter_by_id(
                target_id,
                os.getenv("SENTINEL_APPLICATION_DIRECTORY", "config/applications"),
            )

            class BrowserFormTransport:
                async def form_login(self, credentials: FormCredentials) -> AuthMaterial:
                    response = await browser_context.request.post(
                        f"{base_url}{credentials.login_path}",
                        form=credentials.fields,
                        fail_on_status_code=False,
                    )
                    if response.status not in credentials.expected_statuses:
                        raise PermissionError(
                            f"form login returned unexpected status {response.status}"
                        )
                    cookies = await browser_context.cookies()
                    return AuthMaterial(
                        cookies={str(item["name"]): str(item["value"]) for item in cookies}
                    )

            material = await adapter.authentication_for(persona_id).prepare(
                BrowserFormTransport(), EnvironmentSecretResolver()
            )
            headers = dict(material.headers)
            if target_id == "atlas-local":
                headers["x-atlas-persona"] = persona_id
            if headers:
                await browser_context.set_extra_http_headers(headers)
            await browser_context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = await browser_context.new_page()
            await page.goto(base_url, wait_until="networkidle")
            observer = PlaywrightObserver(
                page=page,
                run_id=run_id,
                session_id=uuid4(),
                persona=PersonaRef(id=persona_id, role=role),
                artifacts=self.artifacts,
                environment={"browser": browser_name, **browser_settings},
            )
            parsed = urlsplit(base_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            allowed_origins.add(origin)
            session = BrowserSession(
                context=browser_context,
                observer=observer,
                executor=BrowserExecutor(page, observer, allowed_origins),
            )
            self._sessions[session_key] = session
        observation = await session.observer.observe()
        for control in observation.controls:
            self._control_sessions[control.id] = session_key
        return observation.model_dump(mode="json")

    async def execute(self, candidate: dict[str, Any]) -> dict[str, Any]:
        action = CandidateAction.model_validate(candidate)
        if action.control_id is None:
            raise ValueError("browser action requires a control id")
        session_key = self._control_sessions[action.control_id]
        session = self._sessions[session_key]
        before = await session.observer.observe()
        execution = await session.executor.execute(action)
        after = await session.observer.observe()
        with tempfile.TemporaryDirectory(prefix="sentinelqa-trace-") as directory:
            trace_path = Path(directory) / "trace.zip"
            await session.context.tracing.stop(path=trace_path)
            trace = self.artifacts.put(
                kind="playwright_trace",
                content=trace_path.read_bytes(),
                mime_type="application/zip",
            )
        after = after.model_copy(
            update={
                "evidence": EvidenceRefs(
                    screenshot_artifact_id=after.evidence.screenshot_artifact_id,
                    trace_chunk_artifact_id=trace.uri,
                    dom_artifact_id=after.evidence.dom_artifact_id,
                )
            }
        )
        await session.context.close()
        self._sessions.pop(session_key, None)
        for control_id, key in list(self._control_sessions.items()):
            if key == session_key:
                self._control_sessions.pop(control_id, None)
        return {
            "executed": True,
            "candidate": candidate,
            "policy_violation": False,
            "before": before.model_dump(mode="json"),
            "after": after.model_dump(mode="json"),
            "outcome": "succeeded",
            **execution,
        }
