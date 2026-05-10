"""
Robust HTTP-only login for webook.com — V16.5+

Why this exists:
  The previous `harvest_web_session` in auth_service.py used `nodriver` to
  open the login page in a headless Chrome and *hoped* that a JWT would
  appear in localStorage. That never happened for fresh accounts (it only
  opens /en/login, never submits credentials), so the function always
  returned None on the cookies path → leaked downstream as
  "cannot unpack non-iterable NoneType object".

This module does the right thing:
  1) Solves Google reCAPTCHA v3 (login action) via 2Captcha.
  2) Submits POST https://api.webook.com/api/v2/login through curl_cffi
     (StealthClient) so the TLS/JA3 fingerprint matches a real browser
     and Cloudflare lets the request through.
  3) Persists the resulting access token + user_id via storage.save_tokens.

Public entry point:
    res = await robust_http_login(account_id)
    res = {"ok": True, "access_token": "...", "expires_at": float, "user_id": "..."}
    res = {"ok": False, "error": "...", "stage": "captcha|login|verify"}

When CAPTCHA_API_KEY is not configured, the function degrades gracefully
and returns a clear, actionable error message instead of raising.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any, Optional

from app.core.config import (
    WEBOOK_API,
    WEBOOK_LANG,
    WEBOOK_ORIGIN,
    WEBOOK_PUBLIC_TOKEN,
    proxy_password,
    proxy_server,
    proxy_username,
    two_captcha_api_key,
)
from app.core.storage import get_account, save_tokens, set_account_status
from app.services.stealth_client import StealthClient

log = logging.getLogger("login_robust")

WEBOOK_RECAPTCHA_SITE_KEY = "6LcvYHooAAAAAC-G46bpymJKtIwfDQpg9DsHPMpL"
WEBOOK_LOGIN_PAGE = f"{WEBOOK_ORIGIN}/en/login"
WEBOOK_LOGIN_API = f"{WEBOOK_API}/login"

TWO_CAPTCHA_IN = "https://2captcha.com/in.php"
TWO_CAPTCHA_RES = "https://2captcha.com/res.php"

# Cap how long we wait for 2Captcha to solve a recaptcha v3 challenge.
CAPTCHA_POLL_INTERVAL = 5.0
CAPTCHA_MAX_WAIT = 180.0


# ════════════════════════════════════════════════════════════════════════
# JWT helpers (stand-alone copies so this module has no circular import)
# ════════════════════════════════════════════════════════════════════════
def _jwt_payload(token: str) -> Optional[dict]:
    try:
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


# ════════════════════════════════════════════════════════════════════════
# Proxy helper (compatible with curl_cffi proxy URL syntax)
# ════════════════════════════════════════════════════════════════════════
def _build_proxy_url() -> Optional[str]:
    server = (proxy_server() or "").strip()
    if not server:
        return None
    if "://" not in server:
        server = f"http://{server}"
    user = (proxy_username() or "").strip()
    pwd = (proxy_password() or "").strip()
    if user and "@" not in server.split("://", 1)[1]:
        # inject creds
        scheme, rest = server.split("://", 1)
        return f"{scheme}://{user}:{pwd}@{rest}"
    return server


# ════════════════════════════════════════════════════════════════════════
# 2Captcha — solve reCAPTCHA v3 (action=login)
# ════════════════════════════════════════════════════════════════════════
async def _solve_recaptcha_v3(
    *,
    api_key: str,
    site_key: str = WEBOOK_RECAPTCHA_SITE_KEY,
    page_url: str = WEBOOK_LOGIN_PAGE,
    action: str = "login",
    min_score: float = 0.3,
) -> tuple[bool, str]:
    """Submit a v3 task to 2Captcha and poll for the solution token.

    Returns (ok, token_or_error_message).
    """
    if not api_key:
        return False, "CAPTCHA_API_KEY not set — cannot solve reCAPTCHA"

    # We use a plain (non-impersonating) HTTP client for 2Captcha — they
    # don't fingerprint and we want low overhead.
    try:
        from curl_cffi.requests import AsyncSession  # type: ignore
    except Exception as e:
        return False, f"curl_cffi unavailable: {e}"

    submit_data = {
        "key": api_key,
        "method": "userrecaptcha",
        "version": "v3",
        "min_score": min_score,
        "action": action,
        "googlekey": site_key,
        "pageurl": page_url,
        "json": 1,
    }

    async with AsyncSession(timeout=30) as s:
        try:
            r = await s.post(TWO_CAPTCHA_IN, data=submit_data)
            payload = r.json()
        except Exception as e:
            return False, f"2captcha submit failed: {e}"

        if str(payload.get("status")) != "1":
            return False, f"2captcha refused task: {payload.get('request') or payload}"

        captcha_id = str(payload.get("request") or "").strip()
        if not captcha_id:
            return False, "2captcha returned empty task id"

        log.info("🧩 2captcha task accepted id=%s — polling…", captcha_id)
        deadline = time.time() + CAPTCHA_MAX_WAIT
        while time.time() < deadline:
            await asyncio.sleep(CAPTCHA_POLL_INTERVAL)
            try:
                rr = await s.get(
                    TWO_CAPTCHA_RES,
                    params={
                        "key": api_key,
                        "action": "get",
                        "id": captcha_id,
                        "json": 1,
                    },
                )
                pr = rr.json()
            except Exception as e:
                log.debug("2captcha poll err (will retry): %s", e)
                continue

            status = str(pr.get("status"))
            req = pr.get("request") or ""
            if status == "1":
                log.info("✅ 2captcha solved task id=%s", captcha_id)
                return True, str(req)
            if req == "CAPCHA_NOT_READY":
                continue
            # Any other request value is a terminal error
            return False, f"2captcha error: {req}"

        return False, "2captcha timed out"


# ════════════════════════════════════════════════════════════════════════
# Webook login over StealthClient
# ════════════════════════════════════════════════════════════════════════
async def _stealth_kwargs() -> dict[str, Any]:
    proxy_url = _build_proxy_url()
    return {
        "proxy_url": proxy_url,
        "fingerprint_seed": "login",
    }


async def _post_login(
    email: str, password: str, captcha_token: str
) -> tuple[int, dict[str, Any]]:
    """POST to /api/v2/login. Returns (status_code, parsed_body_dict)."""
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "token": WEBOOK_PUBLIC_TOKEN
        or "e9aac1f2f0b6c07d6be070ed14829de684264278359148d6a582ca65a50934d2",
        "authorization": "Bearer",
        "accept-language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "origin": WEBOOK_ORIGIN,
        "referer": f"{WEBOOK_ORIGIN}/",
    }
    body = {
        "email": email,
        "password": password,
        "captcha": captcha_token,
        "lang": "en",
    }

    kwargs = await _stealth_kwargs()
    # If no proxy is configured, bypass the StealthClient kill-switch by
    # going through a direct AsyncSession. This is only used for /login,
    # which is hosted on Cloudflare but accepts curl_cffi traffic.
    if not kwargs.get("proxy_url"):
        from curl_cffi.requests import AsyncSession  # type: ignore

        async with AsyncSession(impersonate="chrome131", timeout=30) as s:
            r = await s.post(WEBOOK_LOGIN_API, headers=headers, json=body)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"raw": (r.text or "")[:1500]}

    async with StealthClient(**kwargs) as cli:
        status, parsed = await cli.post_json(
            WEBOOK_LOGIN_API, headers=headers, json=body
        )
        if not isinstance(parsed, dict):
            parsed = {"raw": str(parsed)[:1500]}
        return status, parsed


# ════════════════════════════════════════════════════════════════════════
# Public entry point
# ════════════════════════════════════════════════════════════════════════
async def robust_http_login(account_id: str) -> dict[str, Any]:
    """End-to-end HTTP login for a stored account.

    Looks up the account credentials in the DB, solves recaptcha via
    2Captcha, calls the webook login API, persists the new tokens, and
    returns a structured result.
    """
    acc = await get_account(account_id)
    if not acc:
        return {"ok": False, "error": "Account not found", "stage": "lookup"}

    email = (acc.get("email") or "").strip()
    password = acc.get("password") or ""
    if not email or not password:
        await set_account_status(account_id, "needs_relogin", "missing credentials")
        return {
            "ok": False,
            "error": "البريد الإلكتروني أو كلمة المرور غير مخزّنة بشكل سليم.",
            "stage": "lookup",
        }

    api_key = (two_captcha_api_key() or "").strip()
    if not api_key:
        # Graceful, descriptive failure — do not crash, do not leak NoneType errors.
        await set_account_status(
            account_id,
            "needs_relogin",
            "CAPTCHA_API_KEY not configured",
        )
        return {
            "ok": False,
            "error": (
                "CAPTCHA_API_KEY غير مضبوط في متغيّرات Railway. "
                "أضِف مفتاح 2captcha ثم أعد تسجيل الدخول."
            ),
            "stage": "captcha",
        }

    await set_account_status(account_id, "logging_in")

    # 1) Solve reCAPTCHA v3
    ok, captcha_token = await _solve_recaptcha_v3(
        api_key=api_key,
        site_key=WEBOOK_RECAPTCHA_SITE_KEY,
        page_url=WEBOOK_LOGIN_PAGE,
        action="login",
    )
    if not ok or not captcha_token:
        msg = captcha_token or "captcha solve failed"
        await set_account_status(account_id, "needs_relogin", msg[:200])
        return {"ok": False, "error": msg, "stage": "captcha"}

    # 2) POST /login
    try:
        status, payload = await _post_login(email, password, captcha_token)
    except Exception as e:
        err = f"login network error: {type(e).__name__}: {e}"
        log.error(err)
        await set_account_status(account_id, "needs_relogin", err[:200])
        return {"ok": False, "error": err, "stage": "login"}

    if status != 200:
        err = f"HTTP {status} from webook"
        await set_account_status(account_id, "needs_relogin", err[:200])
        return {"ok": False, "error": err, "stage": "login"}

    if str(payload.get("status")) != "success":
        err_obj = payload.get("error") or payload.get("message") or payload
        # webook returns errors like {"captcha":["Please complete the recaptcha"]}
        # or {"email":["..."]} — flatten for display.
        if isinstance(err_obj, dict):
            flat = []
            for k, v in err_obj.items():
                vs = v if isinstance(v, list) else [v]
                flat.append(f"{k}: {' | '.join(str(x) for x in vs)}")
            err_msg = " ; ".join(flat)
        else:
            err_msg = str(err_obj)
        err_msg = (err_msg or "login refused")[:300]
        await set_account_status(account_id, "needs_relogin", err_msg)
        return {"ok": False, "error": err_msg, "stage": "login"}

    data = payload.get("data") or {}
    access_token = (data.get("access_token") or "").strip()
    if not access_token:
        await set_account_status(
            account_id, "needs_relogin", "no access_token in response"
        )
        return {
            "ok": False,
            "error": "تم القبول لكن لم يصل access_token من webook.",
            "stage": "login",
        }

    expires_at = _jwt_expiry(access_token) or (time.time() + 3600)
    user_id = data.get("_id") or _jwt_sub(access_token) or ""

    # 3) Persist
    try:
        await save_tokens(
            account_id=account_id,
            access=access_token,
            refresh="",
            expires_at=expires_at,
            user_id=user_id,
        )
    except Exception as e:
        # Tokens still valid, but DB write failed — surface it cleanly.
        log.error("save_tokens failed for %s: %s", account_id, e)

    log.info("✅ HTTP login success for %s (user_id=%s)", account_id, user_id)
    return {
        "ok": True,
        "access_token": access_token,
        "expires_at": expires_at,
        "user_id": user_id,
    }
