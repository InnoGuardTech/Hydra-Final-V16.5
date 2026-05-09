"""
Network Layer - Hydra Stealth Upgrade (Phase 1)
Provides a hardened, TLS-fingerprint-bypassing singleton HTTP client using curl_cffi.
"""
from __future__ import annotations

import logging
from typing import Optional

from curl_cffi.requests import AsyncSession

log = logging.getLogger("network")

class SessionManager:
    """Manages isolated stealth sessions for a fleet of accounts."""
    
    def __init__(self):
        self._sessions: dict[str, AsyncSession] = {}

    def get_session(self, account_id: str, proxy_url: Optional[str] = None) -> AsyncSession:
        """Retrieves or creates an isolated session for the given account."""
        if account_id in self._sessions:
            return self._sessions[account_id]

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        
        proxies = None
        if proxy_url:
            proxies = {"http": proxy_url, "https": proxy_url}

        # pool_maxsize equivalent for curl_cffi: max_clients ensures connection limits
        session = AsyncSession(
            impersonate="chrome120",
            headers=headers,
            proxies=proxies,
            timeout=15.0,
            max_clients=2
        )
        
        self._sessions[account_id] = session
        proxy_log = proxy_url.split("@")[-1] if proxy_url and "@" in proxy_url else proxy_url
        log.info(f"🛡️ Session created for {account_id} (Proxy: {proxy_log})")
        return session

    async def close_session(self, account_id: str) -> None:
        """Clean up and free memory after a successful booking or terminal failure."""
        session = self._sessions.pop(account_id, None)
        if session:
            await session.close()
            log.debug(f"🧹 Session closed for {account_id}")

    async def close_all(self) -> None:
        """Close all active sessions."""
        for acc_id, session in list(self._sessions.items()):
            await session.close()
        self._sessions.clear()

# Expose a default instance for the application
session_manager = SessionManager()

# Test snippet (to be executed directly)
if __name__ == "__main__":
    import asyncio
    
    async def _test():
        manager = SessionManager()
        account = "test_account"
        try:
            print(f"[*] Creating session for {account}")
            session = manager.get_session(account)
            response = await session.get("https://httpbin.org/headers")
            print(f"[*] Status Code: {response.status_code}")
            print("[*] Headers seen by server:")
            print(response.json())
        except Exception as e:
            print(f"[!] Error: {e}")
        finally:
            await manager.close_session(account)
            
    asyncio.run(_test())
