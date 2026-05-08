"""
Persistence layer backed by PostgreSQL - Hydra V16.5 (Async Production Build)
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

# استيراد الاتصال الحديث من db.py
from app.core.db import backend as _backend, connect as _conn

log = logging.getLogger("storage")

# أعمدة V12
_V12_COLUMNS = (
    ("royal_category",   "TEXT",                 "TEXT"),
    ("end_date",         "BIGINT DEFAULT 0",     "INTEGER DEFAULT 0"),
    ("has_availability", "INTEGER DEFAULT 1",    "INTEGER DEFAULT 1"),
    ("sub_title",        "TEXT",                 "TEXT"),
    ("venue",            "TEXT",                 "TEXT"),
    ("first_seen_at",    "DOUBLE PRECISION",     "REAL"),
    ("last_seen_at",     "DOUBLE PRECISION",     "REAL"),
    ("last_checked_at",  "DOUBLE PRECISION",     "REAL"),
)

_V14_ACCOUNT_COLUMNS = (("proxy_url", "TEXT", "TEXT"),)

_MIGRATED = False

# 1. تحويل الدالة لتصبح ASYNC وإصلاح السطر 166
async def _ensure_event_v12_columns() -> None:
    global _MIGRATED
    if _MIGRATED: return
    backend_name = _backend()
    try:
        async with _conn() as con: # هذا هو التصحيح للسطر 166
            existing: set[str] = set()
            if backend_name == "postgres":
                # في PostgreSQL نستخدم fetch للحصول على الأسماء
                rows = await con.fetch(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'events'"
                )
                for row in rows:
                    existing.add(row["column_name"])
            
            for col, pg_type, sqlite_type in _V12_COLUMNS:
                if col in existing: continue
                ddl = pg_type if backend_name == "postgres" else sqlite_type
                sql = f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {col} {ddl}"
                await con.execute(sql)
                log.info(f"[migration] events.{col} added")

        _MIGRATED = True
    except Exception as e:
        log.error(f"[migration] V12/V14 failed: {e}")

# 2. تحويل تهيئة القاعدة لتصبح ASYNC
async def init_db() -> None:
    async with _conn() as con:
        # إنشاء الجداول (تم تقسيمها لأن asyncpg لا تدعم executescript)
        await con.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, label TEXT, email TEXT NOT NULL,
                password TEXT NOT NULL, access_token TEXT, status TEXT DEFAULT 'new',
                created_at REAL
            );
        """)
        await con.execute("""
            CREATE TABLE IF NOT EXISTS events (
                slug TEXT PRIMARY KEY, title TEXT, category TEXT,
                royal_category TEXT, has_availability INTEGER DEFAULT 1,
                start_date INTEGER, end_date INTEGER DEFAULT 0
            );
        """)
        await con.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id SERIAL PRIMARY KEY, chat_id TEXT, event_slug TEXT,
                payment_url TEXT, status TEXT, created_at REAL
            );
        """)
    await _ensure_event_v12_columns()

# 3. تحويل دوال التعامل مع البيانات لتصبح ASYNC (مثال: upsert_account)
async def upsert_account(account_id: str, email: str, password: str, label: str = "") -> None:
    async with _conn() as con:
        await con.execute("""
            INSERT INTO accounts (id, label, email, password, status, created_at)
            VALUES ($1, $2, $3, $4, 'new', $5)
            ON CONFLICT(id) DO UPDATE SET
              label = EXCLUDED.label, email = EXCLUDED.email, password = EXCLUDED.password
        """, account_id, label or email.split("@")[0], email, password, time.time())

async def get_account(account_id: str) -> Optional[dict[str, Any]]:
    async with _conn() as con:
        row = await con.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id)
        return dict(row) if row else None

# ════════════════════════════════════════════════════════════════════════
# الدوال المفقودة (تم تحويلها بالكامل إلى Async)
# ════════════════════════════════════════════════════════════════════════

async def delete_account(account_id: str) -> None:
    async with _conn() as con:
        await con.execute("DELETE FROM accounts WHERE id = $1", account_id)

async def save_tokens(account_id: str, access: str, refresh: str, expires_at: float, user_id: Optional[str] = None) -> None:
    async with _conn() as con:
        await con.execute("""
            UPDATE accounts
            SET access_token = $1, refresh_token = $2, token_expires_at = $3,
                user_id = COALESCE($4, user_id), status = 'ready', last_error = NULL
            WHERE id = $5
        """, access, refresh, expires_at, user_id, account_id)

async def set_account_status(account_id: str, status: str, error: Optional[str] = None) -> None:
    async with _conn() as con:
        await con.execute("UPDATE accounts SET status = $1, last_error = $2 WHERE id = $3", status, error, account_id)

async def mark_account_used(account_id: str) -> None:
    async with _conn() as con:
        await con.execute("UPDATE accounts SET last_used_at = $1, tickets_booked = tickets_booked + 1 WHERE id = $2", time.time(), account_id)

async def list_accounts(status: Optional[str] = None) -> list[dict[str, Any]]:
    async with _conn() as con:
        if status:
            rows = await con.fetch("SELECT * FROM accounts WHERE status = $1 ORDER BY created_at ASC", status)
        else:
            rows = await con.fetch("SELECT * FROM accounts ORDER BY created_at ASC")
        return [dict(r) for r in rows]

async def get_bot_setting(key: str, default: str = "") -> str:
    async with _conn() as con:
        row = await con.fetchrow("SELECT value FROM bot_settings WHERE key = $1", key)
        return (row["value"] if row else default) or default

async def set_bot_setting(key: str, value: str, updated_by: str = "admin") -> None:
    async with _conn() as con:
        await con.execute("""
            INSERT INTO bot_settings (key, value, updated_at, updated_by)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT(key) DO UPDATE SET
                value = EXCLUDED.value, updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by
        """, key, value, time.time(), updated_by)

async def list_bot_settings() -> dict[str, str]:
    async with _conn() as con:
        rows = await con.fetch("SELECT key, value FROM bot_settings")
        return {r["key"]: r["value"] for r in rows}

async def add_booking(chat_id: str, event_slug: str, event_title: str, ticket_type: str, account_id: str, quantity: int, seat_info: dict, payment_url: str, total_amount: float = 0.0, currency: str = "SAR", status: str = "pending") -> int:
    async with _conn() as con:
        row = await con.fetchrow("""
            INSERT INTO bookings (chat_id, event_slug, event_title, ticket_type, account_id, quantity, seat_info, payment_url, total_amount, currency, status, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING id
        """, chat_id, event_slug, event_title, ticket_type, account_id, quantity, json.dumps(seat_info, ensure_ascii=False), payment_url, total_amount, currency, status, time.time())
        return row["id"]

async def list_bookings(chat_id: Optional[str] = None, limit: int = 20) -> list[dict[str, Any]]:
    async with _conn() as con:
        if chat_id:
            rows = await con.fetch("SELECT * FROM bookings WHERE chat_id = $1 ORDER BY created_at DESC LIMIT $2", chat_id, limit)
        else:
            rows = await con.fetch("SELECT * FROM bookings ORDER BY created_at DESC LIMIT $1", limit)
        
        out = []
        for r in rows:
            d = dict(r)
            try: d["seat_info"] = json.loads(d.get("seat_info") or "{}")
            except: d["seat_info"] = {}
            out.append(d)
        return out

async def get_event(slug: str) -> Optional[dict[str, Any]]:
    async with _conn() as con:
        row = await con.fetchrow("SELECT * FROM events WHERE slug = $1", slug)
        if not row: return None
        d = dict(row)
        try: d["tickets"] = json.loads(d.get("tickets_json") or "[]")
        except: d["tickets"] = []
        return d

async def list_recent_events(limit: int = 200, royal_category: Optional[str] = None, only_available: bool = True, hide_ended: bool = True) -> list[dict[str, Any]]:
    async with _conn() as con:
        rows = await con.fetch("SELECT * FROM events ORDER BY first_seen_at DESC LIMIT $1", limit)
        return [dict(r) for r in rows]

async def upsert_event(slug: str, data: dict[str, Any]) -> bool:
    now = time.time()
    async with _conn() as con:
        cur = await con.fetchrow("SELECT 1 FROM events WHERE slug = $1", slug)
        is_new = cur is None
        await con.execute("""
            INSERT INTO events (slug, title, category, royal_category, city, url, start_date, end_date, is_seated, has_availability, sub_title, venue, poster, tickets_json, first_seen_at, last_seen_at, last_checked_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
            ON CONFLICT(slug) DO UPDATE SET
              title = EXCLUDED.title, category = EXCLUDED.category, royal_category = EXCLUDED.royal_category,
              city = EXCLUDED.city, url = EXCLUDED.url, start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date,
              is_seated = EXCLUDED.is_seated, has_availability = EXCLUDED.has_availability, sub_title = EXCLUDED.sub_title,
              venue = EXCLUDED.venue, poster = EXCLUDED.poster, tickets_json = EXCLUDED.tickets_json,
              last_seen_at = EXCLUDED.last_seen_at, last_checked_at = EXCLUDED.last_checked_at
        """, slug, data.get("title"), data.get("category"), data.get("royal_category"), data.get("city"), data.get("url"), data.get("start_date"), data.get("end_date") or 0, 1 if data.get("is_seated") else 0, 1 if data.get("has_availability", True) else 0, data.get("sub_title") or "", data.get("venue") or "", data.get("poster"), json.dumps(data.get("tickets") or [], ensure_ascii=False), now, now, now)
        return is_new