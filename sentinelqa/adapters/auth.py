"""Authentication adapters which keep secret values outside configuration and reports."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SecretResolver(Protocol):
    """Resolve an opaque secret reference at the final execution boundary."""

    def resolve(self, reference: str) -> str: ...


class EnvironmentSecretResolver:
    """Resolve ``env:NAME`` references without exposing their values."""

    _name = re.compile(r"^[A-Z][A-Z0-9_]*$")

    def resolve(self, reference: str) -> str:
        if not reference.startswith("env:"):
            raise ValueError("only env: secret references are supported")
        name = reference.removeprefix("env:")
        if not self._name.fullmatch(name):
            raise ValueError("invalid environment secret reference")
        try:
            return os.environ[name]
        except KeyError as exc:
            raise KeyError(f"required secret {reference} is unavailable") from exc


@dataclass(frozen=True, slots=True, repr=False)
class AuthMaterial:
    """Credential material passed directly to a safe transport.

    ``repr`` is deliberately disabled so accidental logs do not disclose credentials.
    """

    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return "AuthMaterial(headers=<redacted>, cookies=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class FormCredentials:
    login_path: str
    fields: dict[str, str]
    expected_statuses: tuple[int, ...]

    def __repr__(self) -> str:
        return f"FormCredentials(login_path={self.login_path!r}, fields=<redacted>)"


class AuthenticationTransport(Protocol):
    async def form_login(self, credentials: FormCredentials) -> AuthMaterial: ...


class AuthAdapter(Protocol):
    async def prepare(
        self, transport: AuthenticationTransport, resolver: SecretResolver
    ) -> AuthMaterial: ...


class _AuthBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoAuthConfig(_AuthBase):
    type: Literal["none"] = "none"


class FormAuthConfig(_AuthBase):
    type: Literal["form"] = "form"
    login_path: str
    username_ref: str
    password_ref: str
    username_field: str = "username"
    password_field: str = "password"  # noqa: S105 - this is a field name, never a credential
    extra_fields: dict[str, str] = Field(default_factory=dict)
    expected_statuses: tuple[int, ...] = (200, 204, 302, 303)

    @field_validator("login_path")
    @classmethod
    def relative_login_path(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("username_ref", "password_ref")
    @classmethod
    def opaque_secret_reference(cls, value: str) -> str:
        return _validate_secret_reference(value)

    @field_validator("extra_fields")
    @classmethod
    def public_extra_fields(cls, value: dict[str, str]) -> dict[str, str]:
        forbidden = {"authorization", "cookie", "password", "secret", "token"}
        if any(key.lower() in forbidden for key in value):
            raise ValueError("credential fields must use secret references")
        return value


class BearerAuthConfig(_AuthBase):
    type: Literal["bearer"] = "bearer"
    token_ref: str

    @field_validator("token_ref")
    @classmethod
    def opaque_secret_reference(cls, value: str) -> str:
        return _validate_secret_reference(value)


class ApiTokenAuthConfig(_AuthBase):
    type: Literal["api_token"] = "api_token"
    token_ref: str
    header_name: str = "X-API-Key"

    @field_validator("header_name")
    @classmethod
    def safe_header_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", value):
            raise ValueError("invalid API token header name")
        return value

    @field_validator("token_ref")
    @classmethod
    def opaque_secret_reference(cls, value: str) -> str:
        return _validate_secret_reference(value)


class OIDCTokenAuthConfig(_AuthBase):
    """Inject a pre-issued test OIDC access token; never contacts a real identity provider."""

    type: Literal["oidc_token"] = "oidc_token"
    access_token_ref: str
    scheme: Literal["Bearer", "DPoP"] = "Bearer"

    @field_validator("access_token_ref")
    @classmethod
    def opaque_secret_reference(cls, value: str) -> str:
        return _validate_secret_reference(value)


AuthConfig = Annotated[
    NoAuthConfig | FormAuthConfig | BearerAuthConfig | ApiTokenAuthConfig | OIDCTokenAuthConfig,
    Field(discriminator="type"),
]


class _NoAuthAdapter:
    async def prepare(
        self, transport: AuthenticationTransport, resolver: SecretResolver
    ) -> AuthMaterial:
        del transport, resolver
        return AuthMaterial()


@dataclass(frozen=True, slots=True)
class _HeaderAuthAdapter:
    reference: str
    header: str
    prefix: str = ""

    async def prepare(
        self, transport: AuthenticationTransport, resolver: SecretResolver
    ) -> AuthMaterial:
        del transport
        token = resolver.resolve(self.reference)
        return AuthMaterial(headers={self.header: f"{self.prefix}{token}"})


@dataclass(frozen=True, slots=True)
class _FormAuthAdapter:
    config: FormAuthConfig

    async def prepare(
        self, transport: AuthenticationTransport, resolver: SecretResolver
    ) -> AuthMaterial:
        fields = dict(self.config.extra_fields)
        fields[self.config.username_field] = resolver.resolve(self.config.username_ref)
        fields[self.config.password_field] = resolver.resolve(self.config.password_ref)
        return await transport.form_login(
            FormCredentials(
                login_path=self.config.login_path,
                fields=fields,
                expected_statuses=self.config.expected_statuses,
            )
        )


def build_auth_adapter(config: AuthConfig) -> AuthAdapter:
    if isinstance(config, NoAuthConfig):
        return _NoAuthAdapter()
    if isinstance(config, FormAuthConfig):
        return _FormAuthAdapter(config)
    if isinstance(config, BearerAuthConfig):
        return _HeaderAuthAdapter(config.token_ref, "Authorization", "Bearer ")
    if isinstance(config, ApiTokenAuthConfig):
        return _HeaderAuthAdapter(config.token_ref, config.header_name)
    if isinstance(config, OIDCTokenAuthConfig):
        return _HeaderAuthAdapter(config.access_token_ref, "Authorization", f"{config.scheme} ")
    raise TypeError(f"unsupported authentication configuration: {type(config).__name__}")


def _validate_relative_path(value: str) -> str:
    if not value.startswith("/") or value.startswith("//") or "://" in value:
        raise ValueError("path must be target-relative")
    if any(part == ".." for part in value.split("/")):
        raise ValueError("path traversal is not allowed")
    return value


def _validate_secret_reference(value: str) -> str:
    if not re.fullmatch(r"env:[A-Z][A-Z0-9_]*", value):
        raise ValueError("credentials must be opaque env:NAME references")
    return value
