"""
Runtime settings persisted in the database (PostgreSQL / SQLite).

Allows the admin web UI to set / update values (e.g. TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID, WEBOOK_PUBLIC_TOKEN) WITHOUT restarting the service
or touching Render's env vars (which lose values on the "replace all"
API).

Resolution order used everywhere in the code:
    1. os.environ  — if set at process boot, takes priority
    2. DB value    — set via the /admin web UI
    3. default     — hard-coded fallback

Values are cached in memory for 10 s to avoid hammering the DB.
"""
from __future__ import annotations

import logging
import os
import time
from threading import RLock
from typing import Any

from app.core.db import connect

log = logging.getLogger("settings")

_lock = RLock()
_cache: dict[str, Any] = {}
_cache_stamp: float = 0.0
_CACHE_TTL = 10.0


async def _init_table() -> None:
    try:
        async with connect() as con:
            await con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key         TEXT PRIMARY KEY,
                value       TEXT,
                updated_at  DOUBLE PRECISION
            );
            """)
    except Exception as e:
        log.error(f"settings table init failed: {e}")


async def sync_legacy_schema(con) -> None:
    """Ensure all required columns exist in the legacy schema."""
    log.info("Checking for missing columns in legacy schema...")
    try:
        # Accounts
        for col in ["last_error", "proxy", "status", "label", "proxy_url"]:
            await con.execute(f"ALTER TABLE accounts ADD COLUMN IF NOT EXISTS {col} TEXT")
        
        # Events (V12 columns)
        await con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS royal_category TEXT")
        await con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS end_date BIGINT DEFAULT 0")
        await con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS has_availability INTEGER DEFAULT 1")
        await con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS sub_title TEXT")
        await con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS venue TEXT")
        await con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS first_seen_at DOUBLE PRECISION")
        await con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS last_seen_at DOUBLE PRECISION")
        await con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS last_checked_at DOUBLE PRECISION")

        # Bookings
        await con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_url TEXT")
        await con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS total_amount DOUBLE PRECISION")
        await con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS currency TEXT")
        await con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS status TEXT")
        await con.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS created_at DOUBLE PRECISION")

        log.info("Schema sync complete.")
    except Exception as e:
        log.warning(f"Schema sync encountered issues (non-critical): {e}")


async def _refresh_cache() -> None:
    global _cache_stamp
    try:
        rows = {}
        async with connect() as con:
            # Using $1 format for consistency with asyncpg
            for r in await con.fetch("SELECT key, value FROM settings"):
                rows[r["key"]] = r["value"]
        with _lock:
            _cache.clear()
            _cache.update(rows)
            _cache_stamp = time.time()
    except Exception as e:
        log.debug(f"settings cache refresh err: {e}")


async def get(key: str, default: str = "") -> str:
    """Read a setting: env → DB → default."""
    env_val = os.environ.get(key)
    if env_val:
        return env_val

    # cache
    if time.time() - _cache_stamp > _CACHE_TTL:
        await _refresh_cache()

    with _lock:
        v = _cache.get(key)
    return v if v not in (None, "") else default


async def set_value(key: str, value: str) -> None:
    """Upsert a setting and invalidate cache."""
    async with connect() as con:
        await con.execute("""
            INSERT INTO settings (key, value, updated_at) VALUES ($1, $2, $3)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value,
                                             updated_at = excluded.updated_at
        """, key, value, time.time())
    global _cache_stamp
    _cache_stamp = 0.0


async def delete(key: str) -> None:
    async with connect() as con:
        await con.execute("DELETE FROM settings WHERE key = $1", key)
    global _cache_stamp
    _cache_stamp = 0.0


async def list_all() -> dict[str, str]:
    """Return all keys (DB only, for admin UI; env wins on read)."""
    await _refresh_cache()
    with _lock:
        return dict(_cache)


# ════════════════════════════════════════════════════════════════════════
# Known well-typed getters (Async)
# ════════════════════════════════════════════════════════════════════════
async def telegram_bot_token() -> str:
    return await get("TELEGRAM_BOT_TOKEN", "")


async def telegram_chat_id() -> str:
    return await get("TELEGRAM_CHAT_ID", "")


async def authorized_chat_ids() -> list[str]:
    raw = await get("AUTHORIZED_CHAT_IDS", "")
    ids = [c.strip() for c in raw.split(",") if c.strip()]
    main = await telegram_chat_id()
    if main and main not in ids:
        ids.append(main)
    return ids


async def webook_public_token() -> str:
    # No hard-coded fallback — must be supplied via env or admin UI.
    return await get("WEBOOK_PUBLIC_TOKEN", "")


async def admin_password() -> str:
    """Password used to open the /admin UI. Must be supplied via env
    or the admin UI — no insecure default value."""
    return await get("ADMIN_PASSWORD", "")


# Fallbacks for PostgreSQL url so we can still bootstrap
async def database_url() -> str:
    return await get("DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
