from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sentinelqa.domain.models import (
    CandidateAction,
    Control,
    EvidenceRefs,
    Observation,
    PageInfo,
    PersonaRef,
    SignalSet,
)
from sentinelqa.exploration.observer import control_id, normalize_route, state_fingerprint
from sentinelqa.storage.artifacts import ArtifactStore

from .locators import resolve_control


@dataclass(slots=True)
class CaptureBuffer:
    console_errors: list[str] = field(default_factory=list)
    failed_requests: list[dict[str, Any]] = field(default_factory=list)
    responses: list[dict[str, Any]] = field(default_factory=list)
    timings: list[dict[str, Any]] = field(default_factory=list)

    def clear(self) -> None:
        self.console_errors.clear()
        self.failed_requests.clear()
        self.responses.clear()
        self.timings.clear()


class PlaywrightObserver:
    def __init__(
        self,
        page: Any,
        run_id: UUID,
        session_id: UUID,
        persona: PersonaRef,
        artifacts: ArtifactStore,
        environment: dict[str, Any] | None = None,
    ) -> None:
        self.page = page
        self.run_id = run_id
        self.session_id = session_id
        self.persona = persona
        self.artifacts = artifacts
        self.environment = dict(environment or {})
        self.buffer = CaptureBuffer()
        self.bindings: dict[str, dict[str, str]] = {}
        page.on("console", self._on_console)
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)

    def _on_console(self, message: Any) -> None:
        if message.type == "error":
            self.buffer.console_errors.append(message.text[:1000])

    def _on_request_failed(self, request: Any) -> None:
        self.buffer.failed_requests.append(
            {"url": request.url, "method": request.method, "failure": str(request.failure)[:500]}
        )

    async def _on_response(self, response: Any) -> None:
        self.buffer.responses.append(
            {"url": response.url, "status": response.status, "method": response.request.method}
        )

    async def observe(self) -> Observation:
        controls_raw = await self.page.locator(
            "a,button,input,select,textarea,[role=button],[role=link],[role=menuitem]"
        ).evaluate_all(
            """els => els.slice(0, 200).map((el, i) => ({
              tag: el.tagName.toLowerCase(),
              role: el.getAttribute('role') ||
                    (el.tagName === 'A' ? 'link' :
                     el.tagName === 'BUTTON' ? 'button' : 'textbox'),
              name: el.getAttribute('aria-label') || el.innerText ||
                    el.getAttribute('placeholder') || el.getAttribute('name') || '',
              test_id: el.getAttribute('data-testid'),
              label: el.labels && el.labels[0] ? el.labels[0].innerText : null,
              enabled: !el.disabled,
              visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
              value: ('value' in el) ? String(el.value).slice(0, 200) : null,
              safety: el.getAttribute('data-safety')
            }))"""
        )
        controls: list[Control] = []
        self.bindings.clear()
        for ordinal, raw in enumerate(controls_raw):
            name = str(raw.get("name", "")).strip()[:200]
            identifier = control_id(str(raw.get("role", raw["tag"])), name, ordinal)
            controls.append(
                Control(
                    id=identifier,
                    type=str(raw.get("role", raw["tag"])),
                    name=name,
                    enabled=bool(raw.get("enabled", True)),
                    visible=bool(raw.get("visible", True)),
                    safety_hint=raw.get("safety") or "safe",
                    value=raw.get("value"),
                )
            )
            self.bindings[identifier] = {
                key: str(value)
                for key, value in {
                    "test_id": raw.get("test_id"),
                    "role": raw.get("role"),
                    "name": name,
                    "label": raw.get("label"),
                }.items()
                if value
            }
        landmarks = await self.page.locator("nav,main,aside,header,footer").evaluate_all(
            "els => [...new Set(els.map(el => el.tagName.toLowerCase()))]"
        )
        accessibility_violations = await self.page.locator("body").evaluate(
            """root => {
              const visible = el => !!(
                el.offsetWidth || el.offsetHeight || el.getClientRects().length
              )
              const name = el => (el.getAttribute('aria-label') ||
                el.getAttribute('aria-labelledby') || el.innerText || el.value || '').trim()
              const issues = []
              root.querySelectorAll('input,select,textarea').forEach((el, index) => {
                const labelled = el.getAttribute('aria-label') ||
                  el.getAttribute('aria-labelledby') || (el.labels && el.labels.length) ||
                  el.getAttribute('title')
                if (visible(el) && !labelled) issues.push({
                  id: 'form-control-name', impact: 'serious',
                  selector: `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`,
                  help: 'Form control has no accessible name'
                })
              })
              root.querySelectorAll('button,a,[role=button],[role=link]').forEach((el, index) => {
                if (visible(el) && !name(el)) issues.push({
                  id: 'interactive-name', impact: 'serious',
                  selector: `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`,
                  help: 'Interactive control has no accessible name'
                })
              })
              root.querySelectorAll('[role=dialog],dialog').forEach((el, index) => {
                if (visible(el) && !name(el)) issues.push({
                  id: 'dialog-name', impact: 'serious',
                  selector: `dialog:nth-of-type(${index + 1})`,
                  help: 'Dialog has no accessible name'
                })
              })
              return issues.slice(0, 100)
            }"""
        )
        screenshot = await self.page.screenshot(full_page=True)
        screenshot_record = self.artifacts.put(
            kind="screenshot", content=screenshot, mime_type="image/png"
        )
        dom = (await self.page.content()).encode()
        dom_record = self.artifacts.put(kind="dom", content=dom, mime_type="text/html", redact=True)
        path = urlsplit(self.page.url).path or "/"
        route = normalize_route(path)
        fingerprint = state_fingerprint(route, landmarks, controls, self.persona.role)
        observation = Observation(
            run_id=self.run_id,
            session_id=self.session_id,
            state_fingerprint=fingerprint,
            page=PageInfo(
                url=self.page.url,
                route=route,
                title=await self.page.title(),
                landmarks=tuple(landmarks),
            ),
            persona=self.persona,
            controls=tuple(controls),
            signals=SignalSet(
                console_errors=tuple(self.buffer.console_errors),
                failed_requests=tuple(self.buffer.failed_requests),
                responses=tuple(self.buffer.responses),
                timings=tuple(self.buffer.timings),
                accessibility_violations=tuple(accessibility_violations),
            ),
            evidence=EvidenceRefs(
                screenshot_artifact_id=screenshot_record.uri,
                dom_artifact_id=dom_record.uri,
            ),
            environment=self.environment,
        )
        self.buffer.clear()
        return observation


class BrowserExecutor:
    def __init__(self, page: Any, observer: PlaywrightObserver, allowed_origins: set[str]) -> None:
        self.page = page
        self.observer = observer
        self.allowed_origins = {origin.rstrip("/") for origin in allowed_origins}

    async def execute(self, action: CandidateAction, timeout_ms: int = 10_000) -> dict[str, Any]:
        if action.control_id is None or action.control_id not in self.observer.bindings:
            raise ValueError("action must reference a current observed control")
        current = urlsplit(self.page.url)
        if f"{current.scheme}://{current.netloc}" not in self.allowed_origins:
            raise PermissionError("page origin is not allowlisted")
        locator, strategy = await resolve_control(
            self.page, self.observer.bindings[action.control_id]
        )
        if not await locator.is_visible() or not await locator.is_enabled():
            raise ValueError("control is no longer executable")
        started = perf_counter()
        if action.action_type == "click":
            await locator.click(timeout=timeout_ms)
        elif action.action_type == "fill":
            await locator.fill(action.input or "", timeout=timeout_ms)
        elif action.action_type == "select":
            await locator.select_option(action.input, timeout=timeout_ms)
        elif action.action_type == "check":
            await locator.check(timeout=timeout_ms)
        elif action.action_type == "uncheck":
            await locator.uncheck(timeout=timeout_ms)
        else:
            raise ValueError(f"unsupported browser action: {action.action_type}")
        redirected = urlsplit(self.page.url)
        redirected_origin = f"{redirected.scheme}://{redirected.netloc}"
        if redirected_origin not in self.allowed_origins:
            raise PermissionError("action redirected to a non-allowlisted origin")
        return {
            "duration_ms": int((perf_counter() - started) * 1000),
            "locator_strategy": strategy,
        }
