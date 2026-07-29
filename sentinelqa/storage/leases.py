from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID


@dataclass(slots=True)
class Lease:
    task_id: UUID
    owner: str
    expires_at: datetime
    heartbeat_at: datetime


class InMemoryLeaseRepository:
    def __init__(self, lease_seconds: int = 45) -> None:
        self.lease_seconds = lease_seconds
        self._leases: dict[UUID, Lease] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, task_id: UUID, owner: str, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        async with self._lock:
            current = self._leases.get(task_id)
            if current and current.expires_at > now and current.owner != owner:
                return False
            self._leases[task_id] = Lease(
                task_id=task_id,
                owner=owner,
                heartbeat_at=now,
                expires_at=now + timedelta(seconds=self.lease_seconds),
            )
            return True

    async def heartbeat(self, task_id: UUID, owner: str, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        async with self._lock:
            lease = self._leases.get(task_id)
            if not lease or lease.owner != owner or lease.expires_at <= now:
                raise ValueError("lease is missing, expired, or owned by another worker")
            lease.heartbeat_at = now
            lease.expires_at = now + timedelta(seconds=self.lease_seconds)

    async def release(self, task_id: UUID, owner: str) -> None:
        async with self._lock:
            lease = self._leases.get(task_id)
            if lease and lease.owner == owner:
                del self._leases[task_id]

    async def recoverable(self, now: datetime | None = None) -> list[UUID]:
        now = now or datetime.now(UTC)
        async with self._lock:
            return sorted(
                [task_id for task_id, lease in self._leases.items() if lease.expires_at <= now],
                key=str,
            )
