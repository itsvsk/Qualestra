from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def lease_loop(poll: Callable[[], Awaitable[bool]], stop: asyncio.Event) -> None:
    while not stop.is_set():
        worked = await poll()
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
