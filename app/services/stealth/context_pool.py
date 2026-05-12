from __future__ import annotations

from contextlib import asynccontextmanager

from app.services.stealth.browser_pool import BrowserPool


class ContextPool:
    def __init__(self, pool: BrowserPool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def lease(self):
        ctx = await self._pool.acquire()
        try:
            yield ctx
        finally:
            await self._pool.release(ctx)
