from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class WorkerPool:
    def __init__(self, concurrency: int = 4) -> None:
        self._sem = asyncio.Semaphore(concurrency)

    async def run(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        async with self._sem:
            return await fn()
