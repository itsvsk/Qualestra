from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from sentinelqa.config import load_yaml
from sentinelqa.domain.models import AgentManifest, Budget
from sentinelqa.policy.capabilities import KNOWN_CAPABILITIES


class RegistryError(ValueError):
    pass


class AgentRegistry:
    def __init__(self, manifests: list[AgentManifest]) -> None:
        self._manifests: dict[tuple[str, str], AgentManifest] = {}
        for manifest in manifests:
            key = (str(manifest.agent_type), manifest.version)
            if key in self._manifests:
                raise RegistryError(f"duplicate agent type/version: {key}")
            unknown = manifest.capabilities - KNOWN_CAPABILITIES
            if unknown:
                raise RegistryError(f"unknown capabilities: {sorted(unknown)}")
            for schema in (manifest.input_schema, manifest.result_schema):
                if not Path(schema).is_file():
                    raise RegistryError(f"missing schema: {schema}")
            self._manifests[key] = manifest

    @classmethod
    def from_file(cls, path: str | Path) -> AgentRegistry:
        raw = load_yaml(path)
        maximums = Budget(**raw["platform_maximums"])
        manifests: list[AgentManifest] = []
        for name, config in raw["agents"].items():
            budget = Budget(**config["budget"])
            for field in Budget.model_fields:
                if getattr(budget, field) > getattr(maximums, field):
                    raise RegistryError(f"{name} {field} exceeds platform maximum")
            manifests.append(
                AgentManifest(
                    agent_type=name,
                    version=config["version"],
                    implementation=config["implementation"],
                    capabilities=frozenset(config["capabilities"]),
                    input_schema=config["input_schema"],
                    result_schema=config["result_schema"],
                    budget=budget,
                    concurrency_class=config["concurrency_class"],
                )
            )
        return cls(manifests)

    def manifest(self, agent_type: str, version: str = "1.0") -> AgentManifest:
        try:
            return self._manifests[(agent_type, version)]
        except KeyError as exc:
            raise RegistryError(f"unregistered agent: {agent_type}@{version}") from exc

    def instantiate(self, agent_type: str, version: str = "1.0") -> Any:
        manifest = self.manifest(agent_type, version)
        module_name, symbol = manifest.implementation.split(":", maxsplit=1)
        implementation = getattr(import_module(module_name), symbol)
        instance = implementation()
        if instance.agent_type != agent_type or instance.version != version:
            raise RegistryError("implementation identity does not match manifest")
        return instance

    @property
    def manifests(self) -> tuple[AgentManifest, ...]:
        return tuple(self._manifests.values())
