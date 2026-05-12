from __future__ import annotations

from typing import Any

from app.core.observability import traced_operation
from app.services.stealth.interfaces import BrowserBackendProtocol
from app.services.stealth.metrics import mark_browser_ok
from app.services.stealth.retry import retry_async


class PlaywrightBackend(BrowserBackendProtocol):
    name = "playwright"

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._pw = None
        self._browser = None

    async def start(self) -> None:
        async def _start() -> None:
            from playwright.async_api import async_playwright
            with traced_operation("stealth.playwright.start"):
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=self._headless)
                mark_browser_ok("startup")
        await retry_async(_start)

    async def stop(self) -> None:
        with traced_operation("stealth.playwright.stop"):
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()
            mark_browser_ok("shutdown")

    async def new_context(self, **kwargs: Any):
        if self._browser is None:
            await self.start()
        assert self._browser is not None
        with traced_operation("stealth.playwright.new_context"):
            ctx = await self._browser.new_context(**kwargs)
            mark_browser_ok("context_create")
            return ctx
