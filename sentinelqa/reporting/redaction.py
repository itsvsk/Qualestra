from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from sentinelqa.policy.redaction import redact_text

REDACTED = "[REDACTED]"
SENSITIVE_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passwd",
        "refresh_token",
        "secret",
        "set_cookie",
        "token",
        "x_api_key",
        "x_test_control_token",
    }
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|"
    r"passwd|credential)\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def redact_report_text(value: str) -> str:
    """Redact credential-shaped text before it can enter a durable report."""

    return SECRET_ASSIGNMENT_PATTERN.sub(REDACTED, redact_text(value))


def redact_report_value(value: Any) -> Any:
    """Recursively redact report data, including tuples and credential-shaped keys."""

    if isinstance(value, str):
        return redact_report_text(value)
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            sanitized[str(key)] = (
                REDACTED if _is_sensitive_key(normalized) else redact_report_value(item)
            )
        return sanitized
    if isinstance(value, tuple):
        return tuple(redact_report_value(item) for item in value)
    if isinstance(value, list):
        return [redact_report_value(item) for item in value]
    return value


def _is_sensitive_key(normalized: str) -> bool:
    if normalized in SENSITIVE_KEY_PARTS:
        return True
    return any(
        normalized.endswith(f"_{suffix}")
        for suffix in ("credential", "password", "secret", "token")
    )
