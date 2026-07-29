from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

Tool = Callable[..., Awaitable[Any]]


class CapabilityDenied(PermissionError):
    pass


class CapabilityScopedToolView:
    def __init__(self, capabilities: frozenset[str], tools: Mapping[str, Tool]) -> None:
        self.capabilities = capabilities
        self._tools = {name: tool for name, tool in tools.items() if name in capabilities}

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    async def call(self, capability: str, **kwargs: Any) -> Any:
        if capability not in self.capabilities or capability not in self._tools:
            raise CapabilityDenied(f"capability denied: {capability}")
        return await self._tools[capability](**kwargs)
