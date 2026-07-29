from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from sentinelqa.domain.enums import AgentType
from sentinelqa.domain.models import AgentTask


@dataclass(frozen=True, slots=True)
class PartitionFilter:
    """Lease-time task filter; empty sets mean no restriction."""

    agent_types: frozenset[AgentType] = frozenset()
    partition_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for prefix in self.partition_prefixes:
            if not prefix or len(prefix) > 160:
                raise ValueError("partition prefixes must contain 1-160 characters")
            if any(character.isspace() for character in prefix):
                raise ValueError("partition prefixes may not contain whitespace")

    def accepts(self, task: AgentTask) -> bool:
        if self.agent_types and AgentType(task.agent_type) not in self.agent_types:
            return False
        return not self.partition_prefixes or task.target_partition.startswith(
            self.partition_prefixes
        )

    def lease_payload(self) -> dict[str, list[str]]:
        """Serializable hook for a queue lease endpoint without changing AgentTask."""

        return {
            "agent_types": sorted(str(agent_type) for agent_type in self.agent_types),
            "partition_prefixes": list(self.partition_prefixes),
        }


@dataclass(frozen=True, slots=True)
class WorkerPoolConfig:
    """Operational worker-pool settings; logical-agent semantics remain unchanged."""

    name: str = "general"
    capacity: int = 4
    task_filter: PartitionFilter = PartitionFilter()
    browser_capable: bool = True
    reset_exclusive: bool = False

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 64:
            raise ValueError("worker pool names must contain 1-64 characters")
        if not 1 <= self.capacity <= 128:
            raise ValueError("worker pool capacity must be between 1 and 128")
        if self.reset_exclusive and self.capacity != 1:
            raise ValueError("reset-exclusive worker pools must have capacity 1")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> WorkerPoolConfig:
        values = environ if environ is not None else os.environ
        raw_types = _csv(values.get("SENTINEL_WORKER_AGENT_TYPES", ""))
        try:
            agent_types = frozenset(AgentType(value) for value in raw_types)
        except ValueError as exc:
            raise ValueError("SENTINEL_WORKER_AGENT_TYPES contains an unknown agent type") from exc
        reset_exclusive = _boolean(values.get("SENTINEL_WORKER_RESET_EXCLUSIVE", "false"))
        return cls(
            name=values.get("SENTINEL_WORKER_POOL", "general"),
            capacity=int(values.get("SENTINEL_WORKER_CONCURRENCY", "4")),
            task_filter=PartitionFilter(
                agent_types=agent_types,
                partition_prefixes=_csv(values.get("SENTINEL_WORKER_PARTITION_PREFIXES", "")),
            ),
            browser_capable=_boolean(values.get("SENTINEL_WORKER_BROWSER_CAPABLE", "true")),
            reset_exclusive=reset_exclusive,
        )


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean setting: {value}")
