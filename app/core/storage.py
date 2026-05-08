"""
Persistence layer — Hydra V16.5
Supports PostgreSQL (asyncpg pool), Turso, and local SQLite.

* init_db() is async — called once at startup from the async event loop.
* All other public functions are synchronous — callers throughout the
  codebase (handlers, orchestrator, drop_watcher, …) invoke them without
  await.  They use connect_sync() which opens a direct connection on
  every call (cheap for SQLite/Turso; uses psycopg2 for Postgres).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from app.core.db import (
    backend as _backend,
    connect as _conn,
    connect_sync as _conn_sync,
)

log = logging.getLogger("storage")

# ── Schema migration column lists ────────────────────────────────────────
_V12_COLUMNS = (
    ("royal_category",   "TEXT",              "TEXT"),
    ("end_date",         "BIGINT DEFAULT 0",  "INTEGER DEFAULT 0"),
    ("has_availability", "INTEGER DEFAULT 1", "INTEGER DEFAULT 1"),
    ("sub_title",        "TEXT",              "TEXT"),
    ("venue",            "TEXT",              "TEXT"),
    ("first_seen_at",    "DOUBLE PRECISION",  "REAL"),
    ("last_seen_at",     "DOUBLE PRECISION",  "REAL"),
    ("last_checked_at",  "DOUBLE PRECISION",  "REAL"),
)

_V14_ACCOUNT_COLUMNS = (("proxy_url", "TEXT", "TEXT"),)

_MIGRATED = False


# ════════════════════════════════════════════════════════════════════════
# Startup — async (called from the event loop once)
# ════════════════════════════════════════════════════════════════════════

def _ensure_event_v12_columns_sync() -> None:
    """Run V12/V14 column migrations synchronously."""
    global _MIGRATED
    if _MIGRATED:
        return
    backend_name = _backend()
    try:
        with _conn_sync() as con:
            existing: set[str] = set()
            if backend_name == "postgres":
                rows = con.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'events'"
                ).fetchall()
                for row in rows:
                    existing.add(row["column_name"] if isinstance(row, dict) else row[0])
            else:
                rows = con.execute("PRAGMA table_info(events)").fetchall()
                for row in rows:
                    existing.add(row["name"] if isinstance(row, dict) else row[1])

            for col, pg_type, sqlite_type in _V12_COLUMNS:
                if col in existing:
                    continue
                ddl = pg_type if backend_name == "postgres" else sqlite_type
                try:
                    con.execute(
                        f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {col} {ddl}"
                    )
                    log.info("[migration] events.%s added", col)
                except Exception as e:
                    log.debug("[migration] events.%s skipped: %s", col, e)

            for col, pg_type, sqlite_type in _V14_ACCOUNT_COLUMNS:
                ddl = pg_type if backend_name == "postgres" else sqlite_type
                try:
                    con.execute(
                        f"ALTER TABLE accounts ADD COLUMN IF NOT EXISTS {col} {ddl}"
                    )
                    log.info("[migration] accounts.%s added", col)
                except Exception as e:
                    log.debug("[migration] accounts.%s skipped: %s", col, e)

        _MIGRATED = True
    except Exception as e:
        log.error("[migration] V12/V14 failed: %s", e)


async def init_db() -> None:
    """Create tables and run migrations.  Called once at startup."""
    async with _conn() as con:
        # asyncpg requires individual statements (no executescript).
        # For SQLite/Turso the async connect() yields a sync wrapper that
        # also handles individual execute() calls fine.
        backend_name = _backend()

        if backend_name == "postgres":
            await con.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, label TEXT, email TEXT NOT NULL,
                    password TEXT NOT NULL, access_token TEXT,
                    proxy_url TEXT,
                    status TEXT DEFAULT 'new', created_at DOUBLE PRECISION
                )
            """)
            await con.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    slug TEXT PRIMARY KEY, title TEXT, category TEXT,
                    royal_category TEXT, has_availability INTEGER DEFAULT 1,
                    start_date BIGINT, end_date BIGINT DEFAULT 0,
                    sub_title TEXT, venue TEXT,
                    first_seen_at DOUBLE PRECISION,
                    last_seen_at DOUBLE PRECISION,
                    last_checked_at DOUBLE PRECISION
                )
            """)
            await con.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    id BIGSERIAL PRIMARY KEY, chat_id TEXT, event_slug TEXT,
                    event_title TEXT, ticket_type TEXT, account_id TEXT,
                    quantity INTEGER DEFAULT 1, seat_info TEXT,
                    payment_url TEXT, total_amount DOUBLE PRECISION,
                    currency TEXT, status TEXT, created_at DOUBLE PRECISION
                )
            """)
            await con.execute("""
                CREATE TABLE IF NOT EXISTS drop_watchers (
                    id BIGSERIAL PRIMARY KEY, chat_id TEXT, account_id TEXT,
                    event_slug TEXT, event_key TEXT,
                    primary_block TEXT, backup_blocks TEXT,
                    quantity INTEGER DEFAULT 1, payment_method TEXT,
                    status TEXT DEFAULT 'watching',
                    created_at DOUBLE PRECISION
                )
            """)
            await con.execute("""
                CREATE TABLE IF NOT EXISTS seat_maps (
                    chart_key TEXT PRIMARY KEY, event_key TEXT,
                    rendering_info TEXT, updated_at DOUBLE PRECISION
                )
            """)
            await con.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY, value TEXT, updated_at DOUBLE PRECISION
                )
            """)
        else:
            # SQLite / Turso — use executescript via the sync wrapper
            con.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, label TEXT, email TEXT NOT NULL,
                    password TEXT NOT NULL, access_token TEXT,
                    proxy_url TEXT,
                    status TEXT DEFAULT 'new', created_at REAL
                );
                CREATE TABLE IF NOT EXISTS events (
                    slug TEXT PRIMARY KEY, title TEXT, category TEXT,
                    royal_category TEXT, has_availability INTEGER DEFAULT 1,
                    start_date INTEGER, end_date INTEGER DEFAULT 0,
                    sub_title TEXT, venue TEXT,
                    first_seen_at REAL, last_seen_at REAL, last_checked_at REAL
                );
                CREATE TABLE IF NOT EXISTS bookings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT, event_slug TEXT, event_title TEXT,
                    ticket_type TEXT, account_id TEXT,
                    quantity INTEGER DEFAULT 1, seat_info TEXT,
                    payment_url TEXT, total_amount REAL,
                    currency TEXT, status TEXT, created_at REAL
                );
                CREATE TABLE IF NOT EXISTS drop_watchers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT, account_id TEXT,
                    event_slug TEXT, event_key TEXT,
                    primary_block TEXT, backup_blocks TEXT,
                    quantity INTEGER DEFAULT 1, payment_method TEXT,
                    status TEXT DEFAULT 'watching',
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS seat_maps (
                    chart_key TEXT PRIMARY KEY, event_key TEXT,
                    rendering_info TEXT, updated_at REAL
                );
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY, value TEXT, updated_at REAL
                );
            """)

    _ensure_event_v12_columns_sync()


# ════════════════════════════════════════════════════════════════════════
# Accounts
# ════════════════════════════════════════════════════════════════════════

def upsert_account(
    account_id: str,
    email: str,
    password: str,
    label: str = "",
    proxy_url: str = "",
) -> None:
    with _conn_sync() as con:
        con.execute(
            """
            INSERT INTO accounts (id, label, email, password, proxy_url, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'new', ?)
            ON CONFLICT(id) DO UPDATE SET
              label = excluded.label,
              email = excluded.email,
              password = excluded.password,
              proxy_url = excluded.proxy_url
            """,
            (account_id, label or email.split("@")[0], email, password,
             proxy_url or "", time.time()),
        )


def get_account(account_id: str) -> Optional[dict[str, Any]]:
    with _conn_sync() as con:
        row = con.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return dict(row) if row else None


def list_accounts() -> list[dict[str, Any]]:
    with _conn_sync() as con:
        rows = con.execute(
            "SELECT * FROM accounts ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_account(account_id: str) -> None:
    with _conn_sync() as con:
        con.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def mark_account_used(account_id: str) -> None:
    with _conn_sync() as con:
        con.execute(
            "UPDATE accounts SET status = 'used' WHERE id = ?", (account_id,)
        )


def update_account_token(account_id: str, token: str) -> None:
    with _conn_sync() as con:
        con.execute(
            "UPDATE accounts SET access_token = ? WHERE id = ?",
            (token, account_id),
        )


# ════════════════════════════════════════════════════════════════════════
# Events
# ════════════════════════════════════════════════════════════════════════

def upsert_event(slug: str, data: dict[str, Any]) -> bool:
    """Insert or update an event.  Returns True if this is a new event."""
    now = time.time()
    with _conn_sync() as con:
        existing = con.execute(
            "SELECT slug FROM events WHERE slug = ?", (slug,)
        ).fetchone()
        is_new = existing is None
        con.execute(
            """
            INSERT INTO events (
                slug, title, category, royal_category, has_availability,
                start_date, end_date, sub_title, venue,
                first_seen_at, last_seen_at, last_checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
              title            = excluded.title,
              category         = excluded.category,
              royal_category   = excluded.royal_category,
              has_availability = excluded.has_availability,
              start_date       = excluded.start_date,
              end_date         = excluded.end_date,
              sub_title        = excluded.sub_title,
              venue            = excluded.venue,
              last_seen_at     = excluded.last_seen_at,
              last_checked_at  = excluded.last_checked_at
            """,
            (
                slug,
                data.get("title", ""),
                data.get("category", ""),
                data.get("royal_category", ""),
                int(data.get("has_availability", 1)),
                data.get("start_date") or 0,
                data.get("end_date") or 0,
                data.get("sub_title", ""),
                data.get("venue", ""),
                data.get("first_seen_at", now) if is_new else now,
                now,
                now,
            ),
        )
        return is_new


def get_event(slug: str) -> Optional[dict[str, Any]]:
    with _conn_sync() as con:
        row = con.execute(
            "SELECT * FROM events WHERE slug = ?", (slug,)
        ).fetchone()
        return dict(row) if row else None


def list_recent_events(limit: int = 100) -> list[dict[str, Any]]:
    with _conn_sync() as con:
        rows = con.execute(
            "SELECT * FROM events ORDER BY last_seen_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def count_events_by_royal_category() -> dict[str, int]:
    with _conn_sync() as con:
        rows = con.execute(
            "SELECT royal_category, COUNT(*) AS cnt FROM events"
            " GROUP BY royal_category"
        ).fetchall()
        return {
            (r["royal_category"] if isinstance(r, dict) else r[0]) or "": (
                r["cnt"] if isinstance(r, dict) else r[1]
            )
            for r in rows
        }


def purge_ended_events(grace_seconds: int = 3600) -> int:
    cutoff = time.time() - grace_seconds
    with _conn_sync() as con:
        cur = con.execute(
            "DELETE FROM events WHERE end_date > 0 AND end_date < ?",
            (int(cutoff),),
        )
        deleted = getattr(cur, "rowcount", 0) or 0
        if deleted:
            log.info("[storage] purged %d ended events", deleted)
        return deleted


# ════════════════════════════════════════════════════════════════════════
# Bookings
# ════════════════════════════════════════════════════════════════════════

def add_booking(
    chat_id: str,
    event_slug: str,
    event_title: str = "",
    ticket_type: str = "",
    account_id: str = "",
    quantity: int = 1,
    seat_info: Optional[dict] = None,
    payment_url: str = "",
    total_amount: float = 0.0,
    currency: str = "",
    status: str = "pending",
) -> Optional[int]:
    seat_json = json.dumps(seat_info or {})
    with _conn_sync() as con:
        cur = con.execute(
            """
            INSERT INTO bookings (
                chat_id, event_slug, event_title, ticket_type, account_id,
                quantity, seat_info, payment_url, total_amount, currency,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(chat_id), event_slug, event_title, ticket_type,
                account_id, quantity, seat_json, payment_url,
                total_amount, currency, status, time.time(),
            ),
        )
        return getattr(cur, "lastrowid", None)


def list_bookings(chat_id: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    with _conn_sync() as con:
        if chat_id:
            rows = con.execute(
                "SELECT * FROM bookings WHERE chat_id = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (str(chat_id), limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM bookings ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════
# Drop watchers
# ════════════════════════════════════════════════════════════════════════

def add_drop_watcher(
    chat_id: str,
    account_id: str,
    event_slug: str,
    event_key: str = "",
    primary_block: str = "",
    backup_blocks: Optional[list[str]] = None,
    quantity: int = 1,
    payment_method: str = "credit_card",
) -> Optional[int]:
    with _conn_sync() as con:
        cur = con.execute(
            """
            INSERT INTO drop_watchers (
                chat_id, account_id, event_slug, event_key,
                primary_block, backup_blocks, quantity, payment_method,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'watching', ?)
            """,
            (
                str(chat_id), account_id, event_slug, event_key or "",
                primary_block or "",
                json.dumps(backup_blocks or []),
                quantity, payment_method, time.time(),
            ),
        )
        return getattr(cur, "lastrowid", None)


def list_drop_watchers(
    status: Optional[str] = None,
    event_key: Optional[str] = None,
) -> list[dict[str, Any]]:
    with _conn_sync() as con:
        if status and event_key:
            rows = con.execute(
                "SELECT * FROM drop_watchers WHERE status = ? AND event_key = ?"
                " ORDER BY created_at ASC",
                (status, event_key),
            ).fetchall()
        elif status:
            rows = con.execute(
                "SELECT * FROM drop_watchers WHERE status = ?"
                " ORDER BY created_at ASC",
                (status,),
            ).fetchall()
        elif event_key:
            rows = con.execute(
                "SELECT * FROM drop_watchers WHERE event_key = ?"
                " ORDER BY created_at ASC",
                (event_key,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM drop_watchers ORDER BY created_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]


def set_drop_watcher_status(watcher_id: int, status: str) -> None:
    with _conn_sync() as con:
        con.execute(
            "UPDATE drop_watchers SET status = ? WHERE id = ?",
            (status, watcher_id),
        )


# ════════════════════════════════════════════════════════════════════════
# Seat maps
# ════════════════════════════════════════════════════════════════════════

def save_seat_map(
    chart_key: str,
    event_key: str,
    rendering_info: Any,
) -> None:
    with _conn_sync() as con:
        con.execute(
            """
            INSERT INTO seat_maps (chart_key, event_key, rendering_info, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chart_key) DO UPDATE SET
              event_key      = excluded.event_key,
              rendering_info = excluded.rendering_info,
              updated_at     = excluded.updated_at
            """,
            (
                chart_key, event_key,
                json.dumps(rendering_info) if not isinstance(rendering_info, str)
                else rendering_info,
                time.time(),
            ),
        )


def get_seat_map(chart_key: str) -> Optional[dict[str, Any]]:
    with _conn_sync() as con:
        row = con.execute(
            "SELECT * FROM seat_maps WHERE chart_key = ?", (chart_key,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["rendering_info"] = json.loads(d["rendering_info"])
        except Exception:
            pass
        return d


# ════════════════════════════════════════════════════════════════════════
# Bot settings (key/value store used by admin UI)
# ════════════════════════════════════════════════════════════════════════

def get_bot_setting(key: str, default: str = "") -> str:
    try:
        with _conn_sync() as con:
            row = con.execute(
                "SELECT value FROM bot_settings WHERE key = ?", (key,)
            ).fetchone()
            if row:
                v = row["value"] if isinstance(row, dict) else row[0]
                return v if v is not None else default
    except Exception:
        pass
    return default


def set_bot_setting(key: str, value: str) -> None:
    with _conn_sync() as con:
        con.execute(
            """
            INSERT INTO bot_settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, time.time()),
        )