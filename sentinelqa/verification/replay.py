from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReplayAttempt:
    attempt: int
    reset_id: str
    matched: bool
    evidence_refs: tuple[str, ...]
    instability: float = 0


class CleanReplayRunner:
    def __init__(
        self,
        reset: Callable[[], Awaitable[str]],
        seed: Callable[[int], Awaitable[None]],
        replay: Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any]]],
    ) -> None:
        self.reset = reset
        self.seed = seed
        self.replay = replay

    async def run(
        self, seed: int, steps: list[dict[str, Any]], attempts: int = 2
    ) -> list[ReplayAttempt]:
        if attempts < 2:
            raise ValueError("reportable anomalies require at least two clean replays")
        output = []
        for index in range(attempts):
            reset_id = await self.reset()
            await self.seed(seed)
            result = await self.replay(steps)
            output.append(
                ReplayAttempt(
                    attempt=index + 1,
                    reset_id=reset_id,
                    matched=bool(result.get("matched")),
                    evidence_refs=tuple(result.get("evidence_refs", [])),
                    instability=float(result.get("instability", 0)),
                )
            )
        return output
