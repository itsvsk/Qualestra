"""Atlas background worker placeholder for export/webhook adapters.

The reference API completes deterministic jobs inline in local mode. This process is retained as
the deployable boundary used when a durable queue is configured.
"""

import asyncio


async def main() -> None:
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
