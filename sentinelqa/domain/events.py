from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from .models import FrozenModel, utc_now


class EventEnvelope(FrozenModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    schema_version: str = "1.0"
    occurred_at: datetime = Field(default_factory=utc_now)
    run_id: UUID
    correlation_id: UUID
    producer: str
    payload: dict[str, Any] = Field(default_factory=dict)
