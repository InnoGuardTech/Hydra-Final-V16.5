from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class AsyncScheduler:
    def __init__(self, rate_limit_delay: float = 0.0) -> None:
        self._rate_limit_delay = rate_limit_delay

    async def schedule(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        if self._rate_limit_delay > 0:
            await asyncio.sleep(self._rate_limit_delay)
        return await fn()
