"""
Central runtime configuration.

Values are resolved strictly from os.environ (Render/Railway variables) at boot.
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()


# ── helpers ───────────────────────────────────────────────────────────────
def _env_or(key: str, default: str = "") -> str:
    """Resolution order: os.environ → default."""
    v = os.getenv(key)
    if v:
        return v
    return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env_or(key, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ── Server (always env-driven) ──────────────────────────────────────────
PORT = int(os.getenv("PORT", "8080"))
HOST = os.getenv("HOST", "0.0.0.0")

# Resolve PUBLIC_URL from any supported platform variable
_railway_public = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
_railway_static = os.getenv("RAILWAY_STATIC_URL", "").strip()
_render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
_explicit = os.getenv("PUBLIC_URL", "").strip()

if _explicit:
    PUBLIC_URL = _explicit
elif _render_url:
    PUBLIC_URL = _render_url
elif _railway_static:
    PUBLIC_URL = _railway_static if _railway_static.startswith("http") else f"https://{_railway_static}"
elif _railway_public:
    PUBLIC_URL = f"https://{_railway_public}"
else:
    PUBLIC_URL = ""

KEEP_ALIVE_ENABLED = os.getenv("KEEP_ALIVE_ENABLED", "true").lower() == "true"
KEEP_ALIVE_INTERVAL = int(os.getenv("KEEP_ALIVE_INTERVAL", "600"))

# ── Webook API ──────────────────────────────────────────────────────────
WEBOOK_ORIGIN = "https://webook.com"
WEBOOK_API = "https://api.webook.com/api/v2"
WEBOOK_LANG = os.getenv("WEBOOK_LANG", "ar")

# ── Monitoring ─────────────────────────────────────────────────────────
EVENT_POLL_INTERVAL = int(os.getenv("EVENT_POLL_INTERVAL", "300"))

LOGIN_CAPTCHA_TIMEOUT = int(os.getenv("LOGIN_CAPTCHA_TIMEOUT", "180"))
TOKEN_REFRESH_MARGIN = int(os.getenv("TOKEN_REFRESH_MARGIN", "300"))

# ── Paths ──────────────────────────────────────────────────────────────
DATA_DIR = os.getenv("DATA_DIR", "data")
DB_PATH = os.getenv("DB_PATH", f"{DATA_DIR}/webook_bot.db")
SESSIONS_DIR = os.getenv("SESSIONS_DIR", "sessions")
LOGS_DIR = os.getenv("LOGS_DIR", "logs")
LOG_FILE = os.getenv("LOG_FILE", f"{LOGS_DIR}/webook_bot.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

for _d in (DATA_DIR, SESSIONS_DIR, LOGS_DIR):
    os.makedirs(_d, exist_ok=True)


# ════════════════════════════════════════════════════════════════════════
# Secrets & Configs
# ════════════════════════════════════════════════════════════════════════
def telegram_bot_token() -> str:
    return _env_or("TELEGRAM_BOT_TOKEN", "")


def telegram_chat_id() -> str:
    return _env_or("TELEGRAM_CHAT_ID", "")


def authorized_chat_ids() -> list[str]:
    raw = os.getenv("AUTHORIZED_CHAT_IDS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def webook_public_token() -> str:
    return _env_or("WEBOOK_PUBLIC_TOKEN", "")


def webook_public_token_builtin_fallback() -> bool:
    raw = _env_or("WEBOOK_PUBLIC_TOKEN_BUILTIN_FALLBACK", "true")
    return (raw or "").strip().lower() in ("1", "true", "yes", "on", "y")


def admin_password() -> str:
    return _env_or("ADMIN_PASSWORD", "")


# ════════════════════════════════════════════════════════════════════════
# Mandatory secret validation
# ════════════════════════════════════════════════════════════════════════
_FORBIDDEN_DEFAULTS = {
    "ADMIN_PASSWORD": {"webook-admin", "admin", "password", "changeme", ""},
}


def validate_required_secrets() -> None:
    import sys

    errors: list[str] = []
    required: dict[str, str] = {
        "ADMIN_PASSWORD": admin_password(),
    }
    if not webook_public_token_builtin_fallback():
        required["WEBOOK_PUBLIC_TOKEN"] = webook_public_token()

    for key, val in required.items():
        v = (val or "").strip()
        if not v:
            errors.append(f"{key} is missing — set it in Render env vars.")
            continue
        bad = _FORBIDDEN_DEFAULTS.get(key, set())
        if v in bad:
            errors.append(
                f"{key} uses a forbidden legacy/default value — "
                f"rotate the secret in Render env vars."
            )
            continue
        if key == "ADMIN_PASSWORD" and len(v) < 8:
            errors.append(
                f"{key} is too short ({len(v)} chars). "
                f"Minimum is 8 characters for production."
            )

    if errors:
        sys.stderr.write(
            "\n\n🛑  V13 STARTUP REFUSED — security validation failed:\n"
        )
        for e in errors:
            sys.stderr.write(f"  • {e}\n")
        sys.stderr.write(
            "\nFix these in your hosting platform's Environment Variables "
            "(Railway / Render / etc.), then redeploy.\n\n"
        )
        sys.exit(78)


# ── Seats.io / SeatCloud runtime tuning ────────────────────────────────
def seatsio_enabled() -> bool:
    return _env_bool("SEATSIO_ENABLED", True)

def seatsio_token_ttl() -> int:
    return int(_env_or("SEATSIO_TOKEN_TTL", "3600"))

def seatsio_prewarm_enabled() -> bool:
    return _env_bool("SEATSIO_PREWARM_ENABLED", True)

def seatsio_status_interval() -> float:
    return float(_env_or("SEATSIO_STATUS_INTERVAL", "0.60"))

def seatsio_drop_watcher_enabled() -> bool:
    return _env_bool("SEATSIO_DROP_WATCHER_ENABLED", True)

def seatsio_drop_watcher_max_wait() -> float:
    return float(_env_or("SEATSIO_DROP_WATCHER_MAX_WAIT", "1800"))

def target_blocks() -> list[str]:
    raw = _env_or("TARGET_BLOCKS", "")
    return [x.strip() for x in raw.split(",") if x.strip()]

def default_payment_method() -> str:
    raw = (_env_or("DEFAULT_PAYMENT_METHOD", "credit_card") or "credit_card").strip().lower()
    return "apple_pay" if raw in {"apple_pay", "applepay", "apple"} else "credit_card"


# ── Paid reliability enhancers ─────────────────────────────────────────
def use_stealth_browser() -> bool:
    return _env_bool("USE_STEALTH_BROWSER", True)

def proxy_server() -> str:
    return _env_or("PROXY_SERVER", "")

def proxy_username() -> str:
    return _env_or("PROXY_USERNAME", "")

def proxy_password() -> str:
    return _env_or("PROXY_PASSWORD", "")

def two_captcha_api_key() -> str:
    return _env_or("CAPTCHA_API_KEY", "")


# ════════════════════════════════════════════════════════════════════════
# V14: HTTP/2 + dynamic-secrets + proxy-per-account knobs
# ════════════════════════════════════════════════════════════════════════
def enable_http2() -> bool:
    return _env_bool("V14_ENABLE_HTTP2", True)

def use_dynamic_secrets() -> bool:
    return _env_bool("V14_USE_DYNAMIC_SECRETS", True)

def secrets_refresh_interval() -> int:
    return int(_env_or("V14_SECRETS_TTL", "3600"))

def default_proxy_url() -> str:
    return _env_or("V14_DEFAULT_PROXY_URL", "").strip()

def stealth_max_connections() -> int:
    return int(_env_or("V14_STEALTH_MAX_CONN", "20"))

def stealth_max_keepalive() -> int:
    return int(_env_or("V14_STEALTH_MAX_KEEPALIVE", "8"))


# ════════════════════════════════════════════════════════════════════════
# Backwards-compatible module-level aliases
# ════════════════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = telegram_bot_token()
TELEGRAM_CHAT_ID = telegram_chat_id()
AUTHORIZED_CHAT_IDS = authorized_chat_ids()
WEBOOK_PUBLIC_TOKEN = webook_public_token()
SEATSIO_ENABLED = seatsio_enabled()
SEATSIO_PREWARM_ENABLED = seatsio_prewarm_enabled()
USE_STEALTH_BROWSER = use_stealth_browser()
DEFAULT_PAYMENT_METHOD = default_payment_method()