"""
Persistence layer backed by PostgreSQL - Hydra V16.5 (Async Production Build)
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from app.core.db import backend as _backend, connect as _conn

log = logging.getLogger("storage")

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

async def _ensure_event_v12_columns() -> None:
    global _MIGRATED
    if _MIGRATED: return
    backend_name = _backend()
    try:
        async with _conn() as con:
            existing: set[str] = set()
            if backend_name == "postgres":
                rows = await con.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'events'")
                for row in rows: existing.add(row["column_name"])
            
            for col, pg_type, sqlite_type in _V12_COLUMNS:
                if col in existing: continue
                ddl = pg_type if backend_name == "postgres" else sqlite_type
                await con.execute(f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {col} {ddl}")
                log.info(f"[migration] events.{col} added")

            # Accounts V14
            existing_acc: set[str] = set()
            if backend_name == "postgres":
                rows = await con.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'accounts'")
                for row in rows: existing_acc.add(row["column_name"])
                
            for col, pg_type, sqlite_type in _V14_ACCOUNT_COLUMNS:
                if col in existing_acc: continue
                ddl = pg_type if backend_name == "postgres" else sqlite_type
                await con.execute(f"ALTER TABLE accounts ADD COLUMN IF NOT EXISTS {col} {ddl}")

        _MIGRATED = True
    except Exception as e:
        log.error(f"[migration] V12/V14 failed: {e}")

async def init_db() -> None:
    async with _conn() as con:
        queries = [
            """CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, label TEXT, email TEXT NOT NULL, password TEXT NOT NULL, access_token TEXT, refresh_token TEXT, token_expires_at REAL DEFAULT 0, user_id TEXT, status TEXT DEFAULT 'new', last_used_at REAL DEFAULT 0, tickets_booked INTEGER DEFAULT 0, last_error TEXT, created_at REAL)""",
            """CREATE TABLE IF NOT EXISTS events (slug TEXT PRIMARY KEY, title TEXT, category TEXT, royal_category TEXT, city TEXT, url TEXT, start_date INTEGER, end_date INTEGER DEFAULT 0, is_seated INTEGER DEFAULT 0, has_availability INTEGER DEFAULT 1, sub_title TEXT, venue TEXT, poster TEXT, tickets_json TEXT, first_seen_at REAL, last_seen_at REAL, last_checked_at REAL)""",
            """CREATE TABLE IF NOT EXISTS bookings (id SERIAL PRIMARY KEY, chat_id TEXT, event_slug TEXT, event_title TEXT, ticket_type TEXT, account_id TEXT, quantity INTEGER, seat_info TEXT, payment_url TEXT, total_amount REAL, currency TEXT, status TEXT, created_at REAL)""",
            """CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT, updated_at REAL, updated_by TEXT)""",
            """CREATE TABLE IF NOT EXISTS event_blocks (id SERIAL PRIMARY KEY, chat_id TEXT, event_slug TEXT, ticket_type_id TEXT, primary_block TEXT, backup_blocks TEXT, quantity INTEGER, payment_method TEXT DEFAULT 'credit_card', created_at REAL)""",
            """CREATE TABLE IF NOT EXISTS drop_watchers (id SERIAL PRIMARY KEY, chat_id TEXT, account_id TEXT, event_slug TEXT, event_key TEXT, ticket_type_id TEXT, quantity INTEGER, blocks_pref TEXT, status TEXT DEFAULT 'watching', created_at REAL, updated_at REAL)""",
            """CREATE TABLE IF NOT EXISTS seat_maps (chart_key TEXT PRIMARY KEY, event_key TEXT, rendering_info TEXT, blocks_meta TEXT, updated_at REAL)""",
            "CREATE INDEX IF NOT EXISTS idx_events_last_seen ON events(last_seen_at)",
            "CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status)",
            "CREATE INDEX IF NOT EXISTS idx_dropwatch_status ON drop_watchers(status)"
        ]
        for q in queries:
            await con.execute(q)
    await _ensure_event_v12_columns()

# ════════════════════════════════════════════════════════════════════════
# Accounts
# ════════════════════════════════════════════════════════════════════════
async def upsert_account(account_id: str, email: str, password: str, label: str = "") -> None:
    async with _conn() as con:
        await con.execute("""
            INSERT INTO accounts (id, label, email, password, status, created_at) VALUES ($1, $2, $3, $4, 'new', $5)
            ON CONFLICT(id) DO UPDATE SET label = EXCLUDED.label, email = EXCLUDED.email, password = EXCLUDED.password
        """, account_id, label or email.split("@")[0], email, password, float(time.time()))

async def save_tokens(account_id: str, access: str, refresh: str, expires_at: float, user_id: Optional[str] = None) -> None:
    async with _conn() as con:
        await con.execute("UPDATE accounts SET access_token = $1, refresh_token = $2, token_expires_at = $3, user_id = COALESCE($4, user_id), status = 'ready', last_error = NULL WHERE id = $5", access, refresh, expires_at, user_id, account_id)

async def set_account_status(account_id: str, status: str, error: Optional[str] = None) -> None:
    async with _conn() as con:
        await con.execute("UPDATE accounts SET status = $1, last_error = $2 WHERE id = $3", status, error, account_id)

async def mark_account_used(account_id: str) -> None:
    async with _conn() as con:
        await con.execute("UPDATE accounts SET last_used_at = $1, tickets_booked = tickets_booked + 1 WHERE id = $2", float(time.time()), account_id)

async def get_account(account_id: str) -> Optional[dict[str, Any]]:
    async with _conn() as con:
        row = await con.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id)
        return dict(row) if row else None

async def list_accounts(status: Optional[str] = None) -> list[dict[str, Any]]:
    async with _conn() as con:
        if status:
            rows = await con.fetch("SELECT * FROM accounts WHERE status = $1 ORDER BY created_at ASC", status)
        else:
            rows = await con.fetch("SELECT * FROM accounts ORDER BY created_at ASC")
        return [dict(r) for r in rows]

async def delete_account(account_id: str) -> None:
    async with _conn() as con:
        await con.execute("DELETE FROM accounts WHERE id = $1", account_id)

# ════════════════════════════════════════════════════════════════════════
# Events
# ════════════════════════════════════════════════════════════════════════
async def upsert_event(slug: str, data: dict[str, Any]) -> bool:
    now = float(time.time())
    await _ensure_event_v12_columns()
    async with _conn() as con:
        cur = await con.fetchrow("SELECT 1 FROM events WHERE slug = $1", slug)
        is_new = cur is None
        await con.execute("""
            INSERT INTO events (slug, title, category, royal_category, city, url, start_date, end_date, is_seated, has_availability, sub_title, venue, poster, tickets_json, first_seen_at, last_seen_at, last_checked_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
            ON CONFLICT(slug) DO UPDATE SET title = EXCLUDED.title, category = EXCLUDED.category, royal_category = EXCLUDED.royal_category, city = EXCLUDED.city, url = EXCLUDED.url, start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date, is_seated = EXCLUDED.is_seated, has_availability = EXCLUDED.has_availability, sub_title = EXCLUDED.sub_title, venue = EXCLUDED.venue, poster = EXCLUDED.poster, tickets_json = EXCLUDED.tickets_json, last_seen_at = EXCLUDED.last_seen_at, last_checked_at = EXCLUDED.last_checked_at
        """, slug, data.get("title"), data.get("category"), data.get("royal_category"), data.get("city"), data.get("url"), data.get("start_date"), data.get("end_date") or 0, 1 if data.get("is_seated") else 0, 1 if data.get("has_availability", True) else 0, data.get("sub_title") or "", data.get("venue") or "", data.get("poster"), json.dumps(data.get("tickets") or [], ensure_ascii=False), now, now, now)
        return is_new

async def purge_ended_events(grace_seconds: int = 3600) -> int:
    cutoff = time.time() - grace_seconds
    async with _conn() as con:
        res = await con.execute("DELETE FROM events WHERE end_date IS NOT NULL AND end_date > 0 AND end_date < $1", cutoff)
        return int(res.split()[-1]) if res else 0

async def get_event(slug: str) -> Optional[dict[str, Any]]:
    async with _conn() as con:
        row = await con.fetchrow("SELECT * FROM events WHERE slug = $1", slug)
        if not row: return None
        d = dict(row)
        try: d["tickets"] = json.loads(d.get("tickets_json") or "[]")
        except: d["tickets"] = []
        return d

async def list_recent_events(limit: int = 200, royal_category: Optional[str] = None, only_available: bool = True, hide_ended: bool = True) -> list[dict[str, Any]]:
    where, params, idx = [], [], 1
    if hide_ended:
        now = time.time()
        where.append(f"(end_date IS NULL OR end_date = 0 OR end_date > ${idx} OR (start_date IS NOT NULL AND start_date > ${idx+1}))")
        params.extend([now - 3600, now - 6 * 3600])
        idx += 2
    if only_available:
        where.append("(has_availability IS NULL OR has_availability = 1)")
    if royal_category:
        where.append(f"royal_category = ${idx}")
        params.append(royal_category)
        idx += 1
    sql = "SELECT * FROM events"
    if where: sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY first_seen_at DESC LIMIT ${idx}"
    params.append(limit)
    
    async with _conn() as con:
        rows = await con.fetch(sql, *params)
        return [dict(r) for r in rows]

async def count_events_by_royal_category(only_available: bool = True, hide_ended: bool = True) -> dict[str, int]:
    where, params, idx = [], [], 1
    if hide_ended:
        now = time.time()
        where.append(f"(end_date IS NULL OR end_date = 0 OR end_date > ${idx} OR (start_date IS NOT NULL AND start_date > ${idx+1}))")
        params.extend([now - 3600, now - 6 * 3600])
        idx += 2
    if only_available:
        where.append("(has_availability IS NULL OR has_availability = 1)")
    sql = "SELECT royal_category, COUNT(*) AS c FROM events"
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY royal_category"
    
    out = {"sports": 0, "concerts": 0, "theater": 0, "experiences": 0, "exhibitions": 0}
    try:
        async with _conn() as con:
            rows = await con.fetch(sql, *params)
            for r in rows:
                key = r["royal_category"] or ""
                cnt = r["c"] or 0
                if key in out: out[key] = int(cnt)
    except: pass
    return out

# ════════════════════════════════════════════════════════════════════════
# Bookings, Settings & Watchers
# ════════════════════════════════════════════════════════════════════════
    async with _conn() as con:
        row = await con.fetchrow("INSERT INTO bookings (chat_id, event_slug, event_title, ticket_type, account_id, quantity, seat_info, payment_url, total_amount, currency, status, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) RETURNING id", chat_id, event_slug, event_title, ticket_type, account_id, quantity, json.dumps(seat_info, ensure_ascii=False), payment_url, total_amount, currency, status, float(time.time()))
        return row["id"]

async def list_bookings(chat_id: Optional[str] = None, limit: int = 20) -> list[dict[str, Any]]:
    async with _conn() as con:
        if chat_id: rows = await con.fetch("SELECT * FROM bookings WHERE chat_id = $1 ORDER BY created_at DESC LIMIT $2", chat_id, limit)
        else: rows = await con.fetch("SELECT * FROM bookings ORDER BY created_at DESC LIMIT $1", limit)
        out = []
        for r in rows:
            d = dict(r)
            try: d["seat_info"] = json.loads(d.get("seat_info") or "{}")
            except: d["seat_info"] = {}
            out.append(d)
        return out

    async with _conn() as con:
        now_ts = float(time.time())
        row = await con.fetchrow("INSERT INTO drop_watchers (chat_id, account_id, event_slug, event_key, ticket_type_id, quantity, blocks_pref, status, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, $7, 'watching', $8, $9) RETURNING id", chat_id, account_id, event_slug, event_key, ticket_type_id, quantity, json.dumps(blocks_pref, ensure_ascii=False), now_ts, now_ts)
        return row["id"]

async def list_drop_watchers(status: Optional[str] = "watching", event_key: Optional[str] = None) -> list[dict[str, Any]]:
    sql, params, idx = "SELECT * FROM drop_watchers WHERE 1=1", [], 1
    if status:
        sql += f" AND status = ${idx}"
        params.append(status)
        idx += 1
    if event_key:
        sql += f" AND event_key = ${idx}"
        params.append(event_key)
        idx += 1
    sql += " ORDER BY created_at"
    async with _conn() as con:
        rows = await con.fetch(sql, *params)
        out = []
        for r in rows:
            d = dict(r)
            try: d["blocks_pref"] = json.loads(d.get("blocks_pref") or "[]")
            except: d["blocks_pref"] = []
            out.append(d)
        return out

async def set_drop_watcher_status(watcher_id: int, status: str) -> None:
    async with _conn() as con:
        await con.execute("UPDATE drop_watchers SET status = $1, updated_at = $2 WHERE id = $3", status, float(time.time()), int(watcher_id))

async def cancel_drop_watchers(chat_id: str) -> int:
    async with _conn() as con:
        res = await con.execute("UPDATE drop_watchers SET status='cancelled', updated_at=$1 WHERE chat_id = $2 AND status='watching'", float(time.time()), chat_id)
        return int(res.split()[-1]) if res else 0

async def set_bot_setting(key: str, value: str, updated_by: str = "admin") -> None:
    async with _conn() as con:
        await con.execute("INSERT INTO bot_settings (key, value, updated_at, updated_by) VALUES ($1, $2, $3, $4) ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by", key, value, float(time.time()), updated_by)

async def get_bot_setting(key: str, default: str = "") -> str:
    async with _conn() as con:
        row = await con.fetchrow("SELECT value FROM bot_settings WHERE key = $1", key)
        return (row["value"] if row else default) or default

async def list_bot_settings() -> dict[str, str]:
    async with _conn() as con:
        rows = await con.fetch("SELECT key, value FROM bot_settings")
        return {r["key"]: r["value"] for r in rows}

async def save_event_blocks(*, chat_id: str, event_slug: str, ticket_type_id: str, primary_block: str, backup_blocks: list[str], quantity: int, payment_method: str = "credit_card") -> int:
    async with _conn() as con:
        row = await con.fetchrow("INSERT INTO event_blocks (chat_id, event_slug, ticket_type_id, primary_block, backup_blocks, quantity, payment_method, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id", chat_id, event_slug, ticket_type_id, primary_block, json.dumps(backup_blocks, ensure_ascii=False), quantity, payment_method, float(time.time()))
        return row["id"]

async def get_event_blocks(blocks_id: int) -> Optional[dict[str, Any]]:
    async with _conn() as con:
        row = await con.fetchrow("SELECT * FROM event_blocks WHERE id = $1", int(blocks_id))
        if not row: return None
        d = dict(row)
        try: d["backup_blocks"] = json.loads(d.get("backup_blocks") or "[]")
        except: d["backup_blocks"] = []
        return d

async def save_seat_map(*, chart_key: str, event_key: str, rendering_info: dict, blocks_meta: list[dict]) -> None:
    async with _conn() as con:
        await con.execute("INSERT INTO seat_maps (chart_key, event_key, rendering_info, blocks_meta, updated_at) VALUES ($1, $2, $3, $4, $5) ON CONFLICT(chart_key) DO UPDATE SET event_key = EXCLUDED.event_key, rendering_info = EXCLUDED.rendering_info, blocks_meta = EXCLUDED.blocks_meta, updated_at = EXCLUDED.updated_at", chart_key, event_key, json.dumps(rendering_info, ensure_ascii=False), json.dumps(blocks_meta, ensure_ascii=False), float(time.time()))

async def get_seat_map(chart_key: str, max_age: float = 86400) -> Optional[dict[str, Any]]:
    async with _conn() as con:
        row = await con.fetchrow("SELECT * FROM seat_maps WHERE chart_key = $1", chart_key)
        if not row: return None
        d = dict(row)
        if (time.time() - float(d.get("updated_at") or 0)) > max_age: return None
        try:
            d["rendering_info"] = json.loads(d.get("rendering_info") or "{}")
            d["blocks_meta"] = json.loads(d.get("blocks_meta") or "[]")
        except: pass
        return d

_ensure_event_v11_columns = _ensure_event_v12_columns