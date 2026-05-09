"""
Authentication service — V15.1 robust HTTP-only path.

Strategies (in order):
  1) Robust HTTP login (login_robust.py) — curl_cffi + 2Captcha, no browser.
     This is the FAST PATH and the only one used in production V15.1+.
  2) Legacy Playwright login — retained as a deep fallback for diagnostics
     when the robust path can't be used (no captcha key, etc.).
  3) Manual JWT paste fallback — unchanged.

After login, everything else stays HTTP-only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

import aiohttp

from app.core.config import (
    HEADLESS,
    TOKEN_REFRESH_MARGIN,
    WEBOOK_API,
    WEBOOK_LANG,
    WEBOOK_ORIGIN,
    WEBOOK_PUBLIC_TOKEN,
    proxy_password,
    proxy_server,
    proxy_username,
    two_captcha_api_key,
    use_stealth_browser,
)
from app.core.storage import get_account, save_tokens, set_account_status

log = logging.getLogger("auth")
WEBOOK_RECAPTCHA_SITE_KEY = "6LcvYHooAAAAAC-G46bpymJKtIwfDQpg9DsHPMpL"

BLOCKED_DOMAINS = (
    "googletagmanager.com", "google-analytics.com", "doubleclick.net",
    "facebook.net", "facebook.com", "amplitude.com", "taboola.com",
    "hotjar.com", "clarity.ms", "twitter.com", "t.co", "linkedin.com",
    "pinterest.com", "tiktok.com", "bing.com", "yandex.ru", "branch.io",
)
BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}

_playwright_err: Optional[Exception] = None
_pw_backend = "playwright"
try:  # Prefer a stealth backend when available.
    if use_stealth_browser():
        from patchright.async_api import async_playwright, TimeoutError as PWTimeout  # type: ignore
        _pw_backend = "patchright"
    else:
        raise ImportError("stealth disabled")
except Exception:
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout  # type: ignore
        _pw_backend = "playwright"
    except Exception as _e:  # pragma: no cover
        _playwright_err = _e
        PWTimeout = Exception  # type: ignore


class AuthError(Exception):
    pass


def _proxy_config() -> Optional[dict[str, str]]:
    server = proxy_server().strip()
    if not server:
        return None
    cfg = {"server": server}
    if proxy_username().strip():
        cfg["username"] = proxy_username().strip()
    if proxy_password().strip():
        cfg["password"] = proxy_password().strip()
    return cfg

async def harvest_web_session(account_id: str, proxy_url: Optional[str] = None) -> dict[str, Any]:
    """Phase 2: The Stealth Harvester using nodriver to bypass Cloudflare."""
    import nodriver as uc
    
    acc = await get_account(account_id)
    if not acc:
        return {"ok": False, "error": "Account not found"}

    log.info(f"🌾 Harvesting web session for account {account_id}")
    await set_account_status(account_id, "harvesting")
    
    browser = None
    try:
        browser_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        if proxy_url:
            browser_args.append(f"--proxy-server={proxy_url}")
            
        browser = await uc.start(
            headless=HEADLESS,
            browser_args=browser_args
        )
        page = await browser.get(f"{WEBOOK_ORIGIN}/en/login")
        
        # Wait for the page to load and Cloudflare challenge to potentially clear
        await asyncio.sleep(10)
        
        # Extract cookies (looking for cf_clearance)
        cookies = await page.send(uc.cdp.network.get_cookies())
        cf_clearance = next((c.value for c in cookies if c.name == 'cf_clearance'), None)
        
        # Check Local Storage for potential JWTs
        local_storage = await page.evaluate("() => JSON.stringify(window.localStorage)")
        storage_data = json.loads(local_storage)
        
        # Attempt to find auth token if logged in automatically or from previous session state
        access_token = storage_data.get("auth_token") or storage_data.get("access_token", "")
        
        if access_token and access_token.lower().startswith("bearer "):
            access_token = access_token[7:].strip()

        if cf_clearance or access_token:
            log.info(f"✅ Harvest successful for {account_id}: cf_clearance found={bool(cf_clearance)}, token found={bool(access_token)}")
            
            # Save the gathered tokens
            expires_at = _jwt_expiry(access_token) if access_token else (time.time() + 3600)
            user_id = _jwt_sub(access_token) if access_token else ""
            
            await save_tokens(
                account_id=account_id,
                access=access_token or "",
                refresh=cf_clearance or "", # Storing cf_clearance in refresh field temporarily for testing
                expires_at=expires_at,
                user_id=user_id
            )
            return {"ok": True, "cf_clearance": cf_clearance, "access_token": access_token}
        else:
            log.warning(f"⚠️ Harvest yielded no critical tokens for {account_id}")
            await set_account_status(account_id, "harvest_failed")
            return {"ok": False, "error": "No tokens extracted."}
            
    except Exception as e:
        log.error(f"❌ Harvester crashed for {account_id}: {e}")
        await set_account_status(account_id, "error", str(e)[:200])
        return {"ok": False, "error": str(e)}
    finally:
        if browser:
            await browser.stop()



async def login_with_manual_token(account_id: str, access_token: str) -> dict[str, Any]:
    acc = await get_account(account_id)
    if not acc:
        return {"ok": False, "error": "الحساب غير موجود"}

    access_token = (access_token or "").strip()
    if access_token.lower().startswith("bearer "):
        access_token = access_token[7:].strip()
    if not access_token.startswith("eyJ") or access_token.count(".") != 2:
        return {"ok": False, "error": "التوكن ليس بصيغة JWT صالحة"}

    expires_at = _jwt_expiry(access_token)
    if not expires_at:
        return {"ok": False, "error": "تعذّر قراءة صلاحية التوكن من محتواه"}
    if expires_at < time.time() + 300:
        return {"ok": False, "error": "التوكن منتهي الصلاحية أو على وشك الانتهاء"}

    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(
                f"{WEBOOK_API}/currencies?lang={WEBOOK_LANG}&visible_in=rs",
                headers={
                    "accept": "application/json",
                    "token": WEBOOK_PUBLIC_TOKEN,
                    "authorization": f"Bearer {access_token}",
                    "accept-language": WEBOOK_LANG,
                    "user-agent": "Mozilla/5.0",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return {"ok": False, "error": f"التوكن غير مقبول من webook ({r.status})"}
        except Exception as e:
            return {"ok": False, "error": f"تعذّر التحقق: {e}"}

    user_id = _jwt_sub(access_token) or ""
    await save_tokens(account_id=account_id, access=access_token, refresh="", expires_at=expires_at, user_id=user_id)
    return {
        "ok": True,
        "tokens": {
            "access_token": access_token,
            "expires_at": expires_at,
            "user_id": user_id,
        },
    }


async def get_valid_bearer(account_id: str, notifier=None, auto_relogin: bool = True) -> Optional[str]:
    acc = await get_account(account_id)
    if not acc:
        return None
    token = acc.get("access_token") or ""
    expires_at = acc.get("token_expires_at") or 0
    if token and time.time() < (expires_at - TOKEN_REFRESH_MARGIN):
        return token
    if not auto_relogin:
        return token or None
    res = await harvest_web_session(account_id)
    if res.get("ok"):
        return res.get("access_token")
    return None


def _jwt_payload(token: str) -> Optional[dict]:
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 3:
            return None
        pad = "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except Exception:
        return None


def _jwt_expiry(token: str) -> Optional[float]:
    p = _jwt_payload(token) or {}
    exp = p.get("exp")
    return float(exp) if exp else None


def _jwt_sub(token: str) -> Optional[str]:
    p = _jwt_payload(token) or {}
    return p.get("sub") or None
