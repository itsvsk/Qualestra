from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SENSITIVE_HEADERS = frozenset(
    {"authorization", "cookie", "set-cookie", "x-api-key", "x-test-control-token"}
)
SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~-]+|sk-[a-z0-9_-]{8,}|password\s*[=:]\s*[^\s,;]+)"
)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        key: "[REDACTED]" if key.lower() in SENSITIVE_HEADERS else redact_text(value)
        for key, value in headers.items()
    }


def redact_text(value: str) -> str:
    return SECRET_PATTERN.sub("[REDACTED]", value)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_HEADERS else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value
