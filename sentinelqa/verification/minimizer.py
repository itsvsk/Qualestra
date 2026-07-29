from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


async def minimize_sequence(
    steps: list[dict[str, Any]],
    still_fails: Callable[[list[dict[str, Any]]], Awaitable[bool]],
) -> list[dict[str, Any]]:
    current = list(steps)
    changed = True
    while changed and len(current) > 1:
        changed = False
        for index in range(len(current)):
            candidate = current[:index] + current[index + 1 :]
            if candidate and await still_fails(candidate):
                current = candidate
                changed = True
                break
    return current
