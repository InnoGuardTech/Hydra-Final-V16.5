"""Centralized environment-backed configuration."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    port: int = Field(8080, alias="PORT")
    host: str = Field("0.0.0.0", alias="HOST")
    public_url: str = Field("", alias="PUBLIC_URL")
    railway_public_domain: str = Field("", alias="RAILWAY_PUBLIC_DOMAIN")
    railway_static_url: str = Field("", alias="RAILWAY_STATIC_URL")
    render_external_url: str = Field("", alias="RENDER_EXTERNAL_URL")

    keep_alive_enabled: bool = Field(True, alias="KEEP_ALIVE_ENABLED")
    keep_alive_interval: int = Field(600, alias="KEEP_ALIVE_INTERVAL")

    webook_lang: str = Field("ar", alias="WEBOOK_LANG")
    event_poll_interval: int = Field(300, alias="EVENT_POLL_INTERVAL")
    login_captcha_timeout: int = Field(180, alias="LOGIN_CAPTCHA_TIMEOUT")
    token_refresh_margin: int = Field(300, alias="TOKEN_REFRESH_MARGIN")

    data_dir: str = Field("data", alias="DATA_DIR")
    db_path: str = Field("data/webook_bot.db", alias="DB_PATH")
    sessions_dir: str = Field("sessions", alias="SESSIONS_DIR")
    logs_dir: str = Field("logs", alias="LOGS_DIR")
    log_file: str = Field("logs/webook_bot.log", alias="LOG_FILE")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    headless: bool = Field(True, alias="HEADLESS")


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    settings = AppSettings()
    for directory in (settings.data_dir, settings.sessions_dir, settings.logs_dir):
        Path(directory).mkdir(parents=True, exist_ok=True)
    return settings


def resolve_public_url(settings: AppSettings) -> str:
    if settings.public_url:
        return settings.public_url
    if settings.render_external_url:
        return settings.render_external_url
    if settings.railway_static_url:
        if settings.railway_static_url.startswith("http"):
            return settings.railway_static_url
        return f"https://{settings.railway_static_url}"
    if settings.railway_public_domain:
        return f"https://{settings.railway_public_domain}"
    return ""
