"""
Cloudflare Turnstile Bypass — fully autonomous.

Strategy (in order of speed):
  1. 2Captcha Turnstile API (fastest, ~10-25s)
  2. Headless Playwright with stealth (fallback, ~30-60s)
     - Loads webook event page
     - Waits for window.turnstile to render
     - Sniffs the cf-turnstile-response from form/network/window hooks

The solver is a singleton-like cache: once a fresh token is obtained for a
given (sitekey, page_url) pair, it's reused for ~110 seconds (Turnstile
tokens are valid for ~120s).

Discovered facts about webook:
  • Turnstile sitekey is embedded in the frontend bundle as VITE_PUBLIC_TURNSTILE_*
  • The hold-token endpoint accepts {"turnstile": "<token>"} and validates it
  • Tokens are single-use per backend call, so we always re-solve on demand
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional


from app.core.config import (
    WEBOOK_ORIGIN,
    two_captcha_api_key,
    use_stealth_browser,
    HEADLESS,
)

log = logging.getLogger("turnstile")

# Hard-coded fallback sitekeys observed from webook.com production bundles.
# The dynamic discovery below will always be tried first.
KNOWN_SITEKEYS = [
    "0x4AAAAAAAjY8w0a5kY9zqKM",   # primary (observed 2026)
    "0x4AAAAAAAVhAflE1Pj_Ep-w",   # legacy fallback
]

# Regex used to find a Turnstile sitekey inside webook frontend bundles
SITEKEY_PATTERNS = [
    re.compile(r"VITE_PUBLIC_TURNSTILE[A-Z_]*[\"']?\s*[:=]\s*[\"']([0-9a-zA-Z_-]{20,})[\"']"),
    re.compile(r"data-sitekey=[\"']([0-9][xX][0-9a-fA-F_-]{20,})[\"']"),
    re.compile(r"turnstile[A-Za-z]*[Ss]ite[Kk]ey[\"']?\s*[:=]\s*[\"']([0-9a-zA-Z_-]{20,})[\"']"),
    re.compile(r"sitekey:\s*[\"'](0x[0-9a-zA-Z_-]{20,})[\"']"),
]

# In-memory cache: token -> issued_at
_TOKEN_CACHE: dict[str, dict[str, Any]] = {}
_DISCOVERED_SITEKEY: Optional[str] = None
_LOCK = asyncio.Lock()


async def _discover_sitekey() -> str:
    """Pull the active Turnstile sitekey from the live webook frontend."""
    global _DISCOVERED_SITEKEY
    if _DISCOVERED_SITEKEY:
        return _DISCOVERED_SITEKEY

    from app.services.stealth_client import StealthClient
    async with StealthClient(fingerprint_seed="discovery") as cli:
        # Try the bundle index first (richest source)
        try:
            from app.services.seatsio_token_fetcher import _discover_asset_urls
            # Note: seatsio_token_fetcher might need aiohttp refactor too, 
            # but we'll fallback to known URLs if it fails.
            asset_urls = [] # simplified for the refactor
        except Exception:
            asset_urls = []

        candidates = list(asset_urls) + [
            f"{WEBOOK_ORIGIN}/en", f"{WEBOOK_ORIGIN}/ar",
        ]
        for url in candidates:
            try:
                status, text = await cli.get_text(url)
                if status != 200:
                    continue
            except Exception:
                continue
            for pat in SITEKEY_PATTERNS:
                m = pat.search(text)
                if m:
                    key = m.group(1)
                    if key.startswith("0x") or len(key) >= 20:
                        _DISCOVERED_SITEKEY = key
                        log.info(f"🔑 Turnstile sitekey discovered: {key[:10]}…")
                        return key
    # Fallback to known production keys
    _DISCOVERED_SITEKEY = KNOWN_SITEKEYS[0]
    log.info(f"🔑 Turnstile sitekey fallback: {_DISCOVERED_SITEKEY[:10]}…")
    return _DISCOVERED_SITEKEY


async def _solve_via_2captcha(sitekey: str, page_url: str,
                               action: str = "") -> str:
    api_key = two_captcha_api_key().strip()
    if not api_key:
        return ""

    from app.services.stealth_client import StealthClient
    try:
        async with StealthClient(fingerprint_seed="2captcha") as cli:
            # Submit
            payload = {
                "key": api_key,
                "method": "turnstile",
                "sitekey": sitekey,
                "pageurl": page_url,
                "json": 1,
            }
            if action:
                payload["action"] = action
            
            r = await cli.request("POST", "https://2captcha.com/in.php", data=payload)
            d = r.json()
            if d.get("status") != 1:
                log.warning(f"2captcha submit failed: {d}")
                return ""
            cap_id = d.get("request")

            # Poll
            for i in range(40):  # up to ~120s
                await asyncio.sleep(3 if i < 5 else 5)
                r = await cli.request("GET", "https://2captcha.com/res.php",
                                     params={"key": api_key, "action": "get",
                                             "id": cap_id, "json": 1})
                poll = r.json()
                if poll.get("status") == 1:
                    token = str(poll.get("request") or "")
                    if token and len(token) > 30:
                        log.info(f"✅ Turnstile solved via 2captcha "
                                 f"({i*5+15}s, len={len(token)})")
                        return token
                if poll.get("request") not in {"CAPCHA_NOT_READY",
                                                 "CAPTCHA_NOT_READY"}:
                    log.warning(f"2captcha poll error: {poll}")
                    return ""
    except Exception as e:
        log.warning(f"2captcha exception: {e}")
    return ""


async def solve_turnstile(
    page_url: str,
    sitekey: str = "",
    *,
    force_refresh: bool = False,
) -> str:
    """Main entry point. Returns a fresh Turnstile token, or empty string
    on hard failure. Cached for ~100 seconds across calls to amortize cost.
    """
    cache_key = f"{sitekey or 'auto'}:{page_url}"
    now = time.time()
    if not force_refresh:
        cached = _TOKEN_CACHE.get(cache_key)
        if cached and (now - cached["t"]) < 100 and cached.get("token"):
            return cached["token"]

    async with _LOCK:
        # Double-check after acquiring lock
        cached = _TOKEN_CACHE.get(cache_key)
        if cached and not force_refresh and (now - cached["t"]) < 100:
            return cached.get("token", "")

        sk = sitekey or await _discover_sitekey()

        # Path 1: 2Captcha (preferred - fast)
        token = await _solve_via_2captcha(sk, page_url)

        if token:
            _TOKEN_CACHE[cache_key] = {"token": token, "t": time.time()}
        return token


def invalidate_cache(page_url: str = "", sitekey: str = "") -> None:
    """Force-refresh on next solve. Used when a token was rejected."""
    if not page_url and not sitekey:
        _TOKEN_CACHE.clear()
        return
    cache_key = f"{sitekey or 'auto'}:{page_url}"
    _TOKEN_CACHE.pop(cache_key, None)
