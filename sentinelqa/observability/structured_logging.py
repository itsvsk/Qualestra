from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sentinelqa.policy.redaction import redact_value


class StructuredLogger:
    """JSON event logger with stable correlation fields and recursive redaction."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def event(
        self,
        event_type: str,
        *,
        run_id: UUID | None = None,
        task_id: UUID | None = None,
        span_id: str | None = None,
        fields: Mapping[str, Any] | None = None,
        level: int = logging.INFO,
    ) -> None:
        payload = {
            "event_type": event_type,
            "run_id": str(run_id) if run_id else None,
            "task_id": str(task_id) if task_id else None,
            "span_id": span_id,
            "fields": redact_value(dict(fields or {})),
        }
        self.logger.log(level, json.dumps(payload, sort_keys=True, separators=(",", ":")))
