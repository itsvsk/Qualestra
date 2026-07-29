from __future__ import annotations

import contextvars
import os
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Protocol, Self

AttributeValue = str | bool | int | float


@dataclass(frozen=True, slots=True)
class SpanData:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    started_at: datetime
    ended_at: datetime
    status: str
    attributes: Mapping[str, AttributeValue]
    exception_type: str | None = None


class Span(Protocol):
    def set_attribute(self, key: str, value: AttributeValue) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class Tracer(Protocol):
    def start_as_current_span(
        self, name: str, attributes: Mapping[str, AttributeValue] | None = None
    ) -> Span: ...


class _NoopSpan:
    def set_attribute(self, key: str, value: AttributeValue) -> None:
        _validate_attribute(key, value)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class NoopTracer:
    def start_as_current_span(
        self, name: str, attributes: Mapping[str, AttributeValue] | None = None
    ) -> Span:
        _validate_span(name, attributes or {})
        return _NoopSpan()


_current_span: contextvars.ContextVar[_InMemorySpan | None] = contextvars.ContextVar(
    "sentinelqa_current_span", default=None
)


class InMemoryTracer:
    """OpenTelemetry-shaped tracer for tests and local use without an SDK or collector."""

    def __init__(self) -> None:
        self._finished: list[SpanData] = []
        self._lock = threading.Lock()

    def start_as_current_span(
        self, name: str, attributes: Mapping[str, AttributeValue] | None = None
    ) -> Span:
        _validate_span(name, attributes or {})
        return _InMemorySpan(self, name, dict(attributes or {}))

    def finished_spans(self) -> tuple[SpanData, ...]:
        with self._lock:
            return tuple(self._finished)

    def _finish(self, span: SpanData) -> None:
        with self._lock:
            self._finished.append(span)


@dataclass(slots=True)
class _InMemorySpan:
    tracer: InMemoryTracer
    name: str
    attributes: dict[str, AttributeValue]
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    _token: contextvars.Token[_InMemorySpan | None] | None = None

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        _validate_attribute(key, value)
        self.attributes[key] = value

    def __enter__(self) -> Self:
        parent = _current_span.get()
        self.trace_id = parent.trace_id if parent else secrets.token_hex(16)
        self.parent_span_id = parent.span_id if parent else None
        self.span_id = secrets.token_hex(8)
        self._token = _current_span.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._token is None:
            raise RuntimeError("span was not entered")
        _current_span.reset(self._token)
        self.tracer._finish(
            SpanData(
                name=self.name,
                trace_id=self.trace_id,
                span_id=self.span_id,
                parent_span_id=self.parent_span_id,
                started_at=self.started_at,
                ended_at=datetime.now(UTC),
                status="error" if exc_type else "ok",
                attributes=dict(self.attributes),
                exception_type=exc_type.__name__ if exc_type else None,
            )
        )


def _validate_span(name: str, attributes: Mapping[str, AttributeValue]) -> None:
    if not name or len(name) > 120:
        raise ValueError("span names must contain 1-120 characters")
    for key, value in attributes.items():
        _validate_attribute(key, value)


def _validate_attribute(key: str, value: AttributeValue) -> None:
    lowered = key.lower()
    if any(part in lowered for part in ("secret", "token", "password", "credential")):
        raise ValueError("secret-bearing trace attributes are forbidden")
    if not key or len(key) > 120:
        raise ValueError("trace attribute names must contain 1-120 characters")
    if isinstance(value, str) and len(value) > 1000:
        raise ValueError("trace string attributes are limited to 1000 characters")


def tracer_from_env(environ: Mapping[str, str] | None = None) -> Tracer:
    values = environ if environ is not None else os.environ
    mode = values.get("SENTINEL_TELEMETRY_MODE", "noop").lower()
    if mode == "noop":
        return NoopTracer()
    if mode == "memory":
        return InMemoryTracer()
    raise ValueError("SENTINEL_TELEMETRY_MODE must be 'noop' or 'memory'")
