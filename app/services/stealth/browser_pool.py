from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from app.services.stealth.interfaces import BrowserBackendProtocol


class BrowserPool:
    def __init__(self, backend: BrowserBackendProtocol, max_contexts: int = 4) -> None:
        self._backend = backend
        self._max_contexts = max_contexts
        self._lock = asyncio.Lock()
        self._available: deque[Any] = deque()
        self._active = 0

    async def acquire(self) -> Any:
        async with self._lock:
            if self._available:
                return self._available.popleft()
            if self._active < self._max_contexts:
                self._active += 1
                return await self._backend.new_context()
        return await self._backend.new_context()

    async def release(self, ctx: Any) -> None:
        async with self._lock:
            self._available.append(ctx)

    async def close(self) -> None:
        async with self._lock:
            while self._available:
                ctx = self._available.popleft()
                await ctx.close()
        await self._backend.stop()
