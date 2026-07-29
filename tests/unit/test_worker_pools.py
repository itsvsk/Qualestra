from __future__ import annotations

import pytest

from sentinelqa.domain.enums import AgentType
from sentinelqa.orchestration.worker_pools import PartitionFilter, WorkerPoolConfig


def test_partition_filter_accepts_only_configured_agent_and_target(task_factory) -> None:
    task_filter = PartitionFilter(
        agent_types=frozenset({AgentType.UI_EXPLORER}),
        partition_prefixes=("atlas:org-a:",),
    )
    accepted = task_factory(AgentType.UI_EXPLORER, target_partition="atlas:org-a:viewer:chromium")
    wrong_agent = task_factory(AgentType.API_EXPLORER, target_partition="atlas:org-a:api:projects")
    wrong_partition = task_factory(
        AgentType.UI_EXPLORER, target_partition="other:org-a:viewer:chromium"
    )

    assert task_filter.accepts(accepted)
    assert not task_filter.accepts(wrong_agent)
    assert not task_filter.accepts(wrong_partition)
    assert task_filter.lease_payload() == {
        "agent_types": ["ui_explorer"],
        "partition_prefixes": ["atlas:org-a:"],
    }


def test_worker_pool_configuration_is_environment_driven() -> None:
    pool = WorkerPoolConfig.from_env(
        {
            "SENTINEL_WORKER_POOL": "api",
            "SENTINEL_WORKER_CONCURRENCY": "12",
            "SENTINEL_WORKER_AGENT_TYPES": "api_explorer,data_invariant",
            "SENTINEL_WORKER_PARTITION_PREFIXES": "atlas:api:,atlas:data:",
            "SENTINEL_WORKER_BROWSER_CAPABLE": "false",
        }
    )

    assert pool.name == "api"
    assert pool.capacity == 12
    assert pool.browser_capable is False
    assert pool.task_filter.agent_types == frozenset(
        {AgentType.API_EXPLORER, AgentType.DATA_INVARIANT}
    )


def test_reset_exclusive_pool_rejects_parallel_capacity() -> None:
    with pytest.raises(ValueError, match="capacity 1"):
        WorkerPoolConfig(name="verification", capacity=2, reset_exclusive=True)
