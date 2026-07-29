import pytest

from sentinelqa.domain.enums import AgentType
from sentinelqa.domain.models import AgentManifest, Budget
from sentinelqa.orchestration.agent_registry import AgentRegistry, RegistryError


def manifest(**updates):
    values = {
        "agent_type": AgentType.CHANGE_IMPACT,
        "implementation": "sentinelqa.agents.change_impact:ChangeImpactAgent",
        "capabilities": frozenset({"read_change_metadata"}),
        "input_schema": "contracts/agent-task.schema.json",
        "result_schema": "contracts/agent-result.schema.json",
        "budget": Budget(),
        "concurrency_class": "read_only",
    }
    values.update(updates)
    return AgentManifest(**values)


def test_registry_loads_all_static_agents():
    registry = AgentRegistry.from_file("config/agents.yaml")
    assert len(registry.manifests) == 6
    assert {str(item.agent_type) for item in registry.manifests} == {
        "change_impact",
        "ui_explorer",
        "api_explorer",
        "data_invariant",
        "verification",
        "defect_triage",
    }
    for item in registry.manifests:
        registry.instantiate(str(item.agent_type))


def test_registry_rejects_duplicate_identity():
    item = manifest()
    with pytest.raises(RegistryError, match="duplicate"):
        AgentRegistry([item, item])


def test_registry_rejects_unknown_capability():
    with pytest.raises(RegistryError, match="unknown capabilities"):
        AgentRegistry([manifest(capabilities=frozenset({"become_supervisor"}))])


def test_registry_rejects_missing_schema():
    with pytest.raises(RegistryError, match="missing schema"):
        AgentRegistry([manifest(input_schema="contracts/missing.json")])
