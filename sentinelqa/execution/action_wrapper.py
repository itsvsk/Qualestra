from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sentinelqa.domain.models import AuthorizedAction, Observation


class ObserverLike(Protocol):
    async def observe(self) -> Observation: ...


class ExecutorLike(Protocol):
    async def execute(self, action: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ObservedActionResult:
    before: Observation
    action: AuthorizedAction
    execution: dict[str, Any]
    after: Observation


class ObservedActionExecutor:
    def __init__(self, observer: ObserverLike, executor: ExecutorLike) -> None:
        self.observer = observer
        self.executor = executor

    async def execute(self, action: AuthorizedAction) -> ObservedActionResult:
        before = await self.observer.observe()
        execution = await self.executor.execute(action.candidate)
        after = await self.observer.observe()
        return ObservedActionResult(before, action, execution, after)
