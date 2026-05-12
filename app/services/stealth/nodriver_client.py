from __future__ import annotations

from typing import Any

from app.services.stealth.interfaces import BrowserBackendProtocol


class NodriverBackend(BrowserBackendProtocol):
    name = "nodriver"

    def __init__(self) -> None:
        self._browser = None

    async def start(self) -> None:
        import nodriver as uc
        self._browser = await uc.start()

    async def stop(self) -> None:
        if self._browser is not None:
            self._browser.stop()

    async def new_context(self, **kwargs: Any):
        if self._browser is None:
            await self.start()
        return self._browser
