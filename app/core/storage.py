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
# 🛑 تحذير: تم حذف سطر init_db() من نهاية الملف لمنع الانهيار 🛑
# ════════════════════════════════════════════════════════════════════════