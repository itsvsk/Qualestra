"""Declarative HTTP invariant DSL and policy-safe executor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from sentinelqa.adapters.auth import AuthMaterial

HttpMethod = Literal["GET", "POST", "PUT", "PATCH"]
AssertionOperator = Literal["equals", "not_equals", "contains", "exists", "length_equals"]


class JsonAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    operator: AssertionOperator = "equals"
    value: JsonValue | None = None

    @field_validator("path")
    @classmethod
    def safe_json_path(cls, value: str) -> str:
        # Deliberately tiny path language: $.field.nested.0. It is data lookup, not code.
        if not value.startswith("$."):
            raise ValueError("JSON path must start with $.")
        forbidden = {"authorization", "cookie", "password", "secret", "token"}
        for segment in value[2:].split("."):
            if not segment or not segment.replace("_", "a").replace("-", "a").isalnum():
                raise ValueError("JSON path supports only names and numeric indexes")
            if segment.lower() in forbidden:
                raise ValueError("JSON assertions may not expose credential-like fields")
        return value

    @model_validator(mode="after")
    def required_value(self) -> JsonAssertion:
        if self.operator not in {"exists"} and self.value is None:
            raise ValueError(f"operator {self.operator} requires a value")
        return self


class HttpRequestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    method: HttpMethod = "GET"
    path: str
    json_body: JsonValue | None = None

    @field_validator("path")
    @classmethod
    def target_relative_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or "://" in value:
            raise ValueError("request path must be target-relative")
        if any(part == ".." for part in value.split("/")):
            raise ValueError("path traversal is not allowed")
        if "{" in value or "}" in value:
            raise ValueError("runtime path interpolation is not supported")
        return value

    @model_validator(mode="after")
    def body_only_for_mutations(self) -> HttpRequestSpec:
        if self.method == "GET" and self.json_body is not None:
            raise ValueError("GET invariants may not have a body")
        return self


class HttpExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: int | tuple[int, ...] = 200
    max_duration_ms: int | None = Field(default=None, gt=0, le=300_000)
    assertions: tuple[JsonAssertion, ...] = Field(default=(), alias="json")

    @field_validator("status")
    @classmethod
    def valid_statuses(cls, value: int | tuple[int, ...]) -> int | tuple[int, ...]:
        statuses = (value,) if isinstance(value, int) else value
        if not statuses or any(status < 100 or status > 599 for status in statuses):
            raise ValueError("expected statuses must be between 100 and 599")
        return value


class HttpInvariant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,79}$")
    description: str = Field(min_length=1, max_length=500)
    severity: Literal["low", "medium", "high", "critical"] = "high"
    persona: str | None = None
    request: HttpRequestSpec
    expect: HttpExpectation


class InvariantDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    invariants: tuple[HttpInvariant, ...]

    @model_validator(mode="after")
    def unique_ids(self) -> InvariantDocument:
        ids = [invariant.id for invariant in self.invariants]
        if len(ids) != len(set(ids)):
            raise ValueError("invariant ids must be unique")
        return self


@dataclass(frozen=True, slots=True)
class SafeHttpResponse:
    status: int
    duration_ms: int
    json_body: JsonValue | None
    evidence_ref: str


class SafeHttpTool(Protocol):
    """A policy-enforcing transport supplied by the runtime, never by an LLM."""

    async def request(
        self,
        method: HttpMethod,
        path: str,
        *,
        json_body: JsonValue | None,
        authentication: AuthMaterial,
    ) -> SafeHttpResponse: ...


@dataclass(frozen=True, slots=True)
class InvariantCheck:
    name: str
    passed: bool
    expected: JsonValue
    actual: JsonValue


@dataclass(frozen=True, slots=True)
class InvariantResult:
    invariant_id: str
    passed: bool
    severity: str
    checks: tuple[InvariantCheck, ...]
    evidence_refs: tuple[str, ...]


class HttpInvariantRunner:
    def __init__(self, tool: SafeHttpTool, *, allow_mutations: bool = False) -> None:
        self._tool = tool
        self._allow_mutations = allow_mutations

    async def execute(
        self, invariant: HttpInvariant, authentication: AuthMaterial | None = None
    ) -> InvariantResult:
        request = invariant.request
        if request.method != "GET" and not self._allow_mutations:
            raise PermissionError("mutating invariants require explicit runtime authorization")
        response = await self._tool.request(
            request.method,
            request.path,
            json_body=request.json_body,
            authentication=authentication or AuthMaterial(),
        )
        expected_statuses = (
            (invariant.expect.status,)
            if isinstance(invariant.expect.status, int)
            else invariant.expect.status
        )
        checks: list[InvariantCheck] = [
            InvariantCheck(
                name="status",
                passed=response.status in expected_statuses,
                expected=list(expected_statuses),
                actual=response.status,
            )
        ]
        if invariant.expect.max_duration_ms is not None:
            checks.append(
                InvariantCheck(
                    name="duration_ms",
                    passed=response.duration_ms <= invariant.expect.max_duration_ms,
                    expected=invariant.expect.max_duration_ms,
                    actual=response.duration_ms,
                )
            )
        for assertion in invariant.expect.assertions:
            found, actual = _lookup(response.json_body, assertion.path)
            passed = _compare(assertion, found, actual)
            checks.append(
                InvariantCheck(
                    name=f"json:{assertion.path}:{assertion.operator}",
                    passed=passed,
                    expected=True if assertion.operator == "exists" else assertion.value,
                    actual=actual if found else "<missing>",
                )
            )
        return InvariantResult(
            invariant_id=invariant.id,
            passed=all(check.passed for check in checks),
            severity=invariant.severity,
            checks=tuple(checks),
            evidence_refs=(response.evidence_ref,),
        )


def load_invariants(path: str | Path) -> InvariantDocument:
    with Path(path).open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    return InvariantDocument.model_validate(value)


def _lookup(document: JsonValue | None, path: str) -> tuple[bool, JsonValue | None]:
    current: Any = document
    for segment in path[2:].split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return False, None
    return True, current


def _compare(assertion: JsonAssertion, found: bool, actual: JsonValue | None) -> bool:
    if assertion.operator == "exists":
        return found
    if not found:
        return False
    if assertion.operator == "equals":
        return actual == assertion.value
    if assertion.operator == "not_equals":
        return actual != assertion.value
    if assertion.operator == "contains":
        if isinstance(actual, (str, list)):
            return assertion.value in actual
        if isinstance(actual, dict) and isinstance(assertion.value, str):
            return assertion.value in actual
        return False
    if assertion.operator == "length_equals":
        return isinstance(actual, (str, list, dict)) and len(actual) == assertion.value
    return False
