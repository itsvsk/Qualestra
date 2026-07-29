"""Typed application/persona contract and explicit adapter registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from sentinelqa.adapters.auth import AuthAdapter, AuthConfig, NoAuthConfig, build_auth_adapter


class PersonaConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    id: str = ""
    name: str
    role: str
    username: str | None = None
    authentication: AuthConfig = Field(default_factory=NoAuthConfig)


class ApplicationConfig(BaseModel):
    """Portable application definition; legacy Atlas fields remain accepted as metadata."""

    model_config = ConfigDict(extra="allow", frozen=True)
    id: str
    name: str = ""
    adapter: str = "static"
    base_url: str
    api_base_url: str
    allowed_origins: tuple[str, ...]
    openapi_url: str | None = None
    health_url: str | None = None
    test_control_url: str | None = None
    persona_file: str | None = None
    invariant_file: str | None = None
    legacy_invariant_file: str | None = None
    personas: dict[str, PersonaConfig] = Field(default_factory=dict)
    risk_areas: dict[str, str] = Field(default_factory=dict)
    destructive_routes: tuple[str, ...] = ()
    critical_journeys: tuple[str, ...] = ()

    @field_validator("base_url", "api_base_url", "openapi_url", "health_url", "test_control_url")
    @classmethod
    def absolute_http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("URL must be an absolute HTTP(S) URL")
        if parts.username or parts.password or parts.fragment:
            raise ValueError("URL may not contain credentials or fragments")
        return value.rstrip("/")

    @field_validator("allowed_origins")
    @classmethod
    def origins_only(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("at least one allowed origin is required")
        normalized: list[str] = []
        for value in values:
            parts = urlsplit(value)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise ValueError("allowed origins must be absolute HTTP(S) origins")
            if parts.path not in {"", "/"} or parts.query or parts.fragment:
                raise ValueError("allowed origin may not contain a path, query, or fragment")
            normalized.append(f"{parts.scheme}://{parts.netloc}")
        return tuple(normalized)

    @model_validator(mode="after")
    def endpoints_are_allowlisted(self) -> ApplicationConfig:
        allowed = set(self.allowed_origins)
        for name in ("base_url", "api_base_url", "openapi_url", "health_url", "test_control_url"):
            value = getattr(self, name)
            if value is None:
                continue
            parts = urlsplit(value)
            if f"{parts.scheme}://{parts.netloc}" not in allowed:
                raise ValueError(f"{name} origin is not allowlisted")
        return self


class ProjectAdapter(Protocol):
    @property
    def application(self) -> ApplicationConfig: ...

    def personas(self) -> Mapping[str, PersonaConfig]: ...

    def authentication_for(self, persona_id: str) -> AuthAdapter: ...


class StaticProjectAdapter:
    def __init__(
        self, application: ApplicationConfig, personas: Mapping[str, PersonaConfig] | None = None
    ) -> None:
        self._application = application
        self._personas = dict(personas or application.personas)

    @property
    def application(self) -> ApplicationConfig:
        return self._application

    def personas(self) -> Mapping[str, PersonaConfig]:
        return dict(self._personas)

    def authentication_for(self, persona_id: str) -> AuthAdapter:
        try:
            persona = self._personas[persona_id]
        except KeyError as exc:
            raise KeyError(f"unknown persona: {persona_id}") from exc
        return build_auth_adapter(persona.authentication)


AdapterFactory = Callable[[ApplicationConfig, Mapping[str, PersonaConfig]], ProjectAdapter]


class ProjectAdapterRegistry:
    """Explicit registry: configuration cannot dynamically import executable code."""

    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {
            "static": lambda app, personas: StaticProjectAdapter(app, personas)
        }

    def register(self, name: str, factory: AdapterFactory) -> None:
        if not name or name in self._factories:
            raise ValueError(f"adapter already registered or invalid: {name!r}")
        self._factories[name] = factory

    def create(
        self, application: ApplicationConfig, personas: Mapping[str, PersonaConfig] | None = None
    ) -> ProjectAdapter:
        try:
            factory = self._factories[application.adapter]
        except KeyError as exc:
            raise ValueError(f"adapter is not registered: {application.adapter}") from exc
        return factory(application, personas or application.personas)


def load_application(path: str | Path) -> ApplicationConfig:
    return ApplicationConfig.model_validate(_load_mapping(path))


def load_personas(path: str | Path) -> dict[str, PersonaConfig]:
    data = _load_mapping(path)
    raw = data.get("personas", data)
    if not isinstance(raw, dict):
        raise ValueError("persona document must contain a personas mapping")
    personas: dict[str, PersonaConfig] = {}
    adapter = TypeAdapter(PersonaConfig)
    for persona_id, value in raw.items():
        if not isinstance(persona_id, str) or not isinstance(value, dict):
            raise ValueError("each persona must be a named mapping")
        personas[persona_id] = adapter.validate_python({"id": persona_id, **value})
    return personas


def load_project_adapter(
    application_path: str | Path, registry: ProjectAdapterRegistry | None = None
) -> ProjectAdapter:
    """Load a project while confining referenced YAML files to the config tree."""

    path = Path(application_path).resolve()
    application = load_application(path)
    personas = application.personas
    if application.persona_file:
        persona_path = _confined_reference(path, application.persona_file)
        personas = load_personas(persona_path)
    return (registry or ProjectAdapterRegistry()).create(application, personas)


def find_application_path(target_id: str, directory: str | Path = "config/applications") -> Path:
    """Find a configured target by its declared ID, rejecting ambiguity.

    File names are intentionally not treated as target IDs. This keeps renames harmless and
    ensures every runtime target passed the same typed validation as the onboarding CLI.
    """

    root = Path(directory).resolve()
    matches = [
        path for path in sorted(root.glob("*.yaml")) if load_application(path).id == target_id
    ]
    if not matches:
        raise ValueError(f"unknown target_id: {target_id}")
    if len(matches) > 1:
        raise ValueError(f"duplicate application id: {target_id}")
    return matches[0]


def load_project_adapter_by_id(
    target_id: str,
    directory: str | Path = "config/applications",
    registry: ProjectAdapterRegistry | None = None,
) -> ProjectAdapter:
    return load_project_adapter(find_application_path(target_id, directory), registry)


def resolve_config_reference(application_path: str | Path, reference: str) -> Path:
    """Resolve a portable config reference without allowing arbitrary filesystem access."""

    return _confined_reference(Path(application_path).resolve(), reference)


def _load_mapping(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected a YAML mapping in {path}")
    return value


def _confined_reference(application_path: Path, reference: str) -> Path:
    if Path(reference).is_absolute():
        raise ValueError("config references must be relative")
    candidate = (application_path.parent / reference).resolve()
    # Portable repository layouts conventionally keep applications/personas/invariants
    # beneath one common config directory. Allow siblings, but never escape that root.
    config_root = (
        application_path.parent.parent.resolve()
        if application_path.parent.name == "applications"
        else application_path.parent.resolve()
    )
    if candidate != config_root and config_root not in candidate.parents:
        raise ValueError("config reference escapes its configuration root")
    return candidate
