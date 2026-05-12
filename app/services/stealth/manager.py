from __future__ import annotations

from app.core.config_loader import get_settings
from app.services.stealth.browser_pool import BrowserPool
from app.services.stealth.context_pool import ContextPool
from app.services.stealth.nodriver_client import NodriverBackend
from app.services.stealth.patchright_client import PatchrightBackend
from app.services.stealth.playwright_client import PlaywrightBackend


class StealthManager:
    def __init__(self) -> None:
        s = get_settings()
        backend_name = getattr(s, "browser_backend", "playwright")
        if backend_name == "patchright":
            backend = PatchrightBackend(headless=s.headless)
        elif backend_name == "nodriver":
            backend = NodriverBackend()
        else:
            backend = PlaywrightBackend(headless=s.headless)
        self.pool = BrowserPool(backend, max_contexts=getattr(s, "browser_max_contexts", 4))
        self.contexts = ContextPool(self.pool)
