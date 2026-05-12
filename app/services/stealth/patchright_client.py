from __future__ import annotations

from app.services.stealth.playwright_client import PlaywrightBackend


class PatchrightBackend(PlaywrightBackend):
    name = "patchright"

    async def start(self) -> None:
        from patchright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)
