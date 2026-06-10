"""
Centralised application settings.

All values are loaded from environment variables / .env file.
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet
from pydantic import PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_MISSING_FERNET_KEY_MESSAGE = (
    "Fernet encryption key is missing. Set FERNET_KEY (preferred) or ENCRYPTION_KEY in your "
    "environment. Generate one with: from cryptography.fernet import Fernet; "
    "print(Fernet.generate_key().decode())"
)

_INVALID_FERNET_KEY_MESSAGE = (
    "Fernet encryption key is invalid. FERNET_KEY/ENCRYPTION_KEY must be a URL-safe "
    "base64-encoded 32-byte key."
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PostgreSQL
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "erp_pilot"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TEXT2SQL_MODEL: str = "gpt-5.4-mini"
    GEMINI_API_KEY: str = ""
    GEMINI_TEXT2SQL_MODEL: str = "gemini-2.5-flash"
    TEXT2SQL_ENABLED: bool = True
    TEXT2SQL_MAX_ROWS: int = 100
    TEXT2SQL_TIMEOUT_SECONDS: int = 5

    # JWT + encryption
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    FERNET_KEY: str = ""
    ENCRYPTION_KEY: str = ""
    AUTO_GENERATE_FERNET_KEY: bool = False
    ODOO_CREDENTIALS_ENCRYPTION_KEY: str = ""  # Backward compatibility fallback
    ODOO_URL: str = "http://localhost:8069"
    ODOO_DB_NAME: str = "odoo"
    ODOO_DB_USER: str = "odoo"
    ODOO_DB_PASSWORD: str = "odoo"
    ODOO_SQL_HOST: str = "odoo-db"
    ODOO_SQL_PORT: int = 5432
    ODOO_SQL_DATABASE: str = "odoo"
    ODOO_SQL_USER: str = "odoo"
    ODOO_SQL_PASSWORD: str = "odoo"

    # Automatic stock alerts
    STOCK_ALERT_ENABLED: bool = True
    STOCK_ALERT_SCAN_INTERVAL_SECONDS: int = 3600
    STOCK_ALERT_FORECAST_DAYS: int = 7
    STOCK_ALERT_SALES_LOOKBACK_DAYS: int = 30

    # Google OAuth2
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"
    FRONTEND_GOOGLE_CALLBACK_URL: str = "http://localhost:5173/auth/google/callback"
    BACKEND_CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    ADMIN_SEED_EMAIL: str = "admin@myerppilot.app"
    ADMIN_SEED_PASSWORD: str = "Admin12345!"
    ADMIN_SEED_FULL_NAME: str = "System Administrator"

    _runtime_fernet_key: str = PrivateAttr(default="")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def odoo_sql_database_url(self) -> str:
        return (
            f"postgresql://{self.ODOO_SQL_USER}:{self.ODOO_SQL_PASSWORD}"
            f"@{self.ODOO_SQL_HOST}:{self.ODOO_SQL_PORT}/{self.ODOO_SQL_DATABASE}"
        )

    @property
    def backend_cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.BACKEND_CORS_ORIGINS.split(",") if origin.strip()]

    def _configured_fernet_key(self) -> str:
        if self._runtime_fernet_key:
            return self._runtime_fernet_key

        for candidate in (
            self.FERNET_KEY,
            self.ENCRYPTION_KEY,
            self.ODOO_CREDENTIALS_ENCRYPTION_KEY,
        ):
            if candidate and candidate.strip():
                return candidate.strip()

        return ""

    @staticmethod
    def validate_fernet_key_format(key: str) -> None:
        try:
            Fernet(key.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - surface a clean config error
            raise ValueError(_INVALID_FERNET_KEY_MESSAGE) from exc

    def get_fernet_key(self) -> str:
        key = self._configured_fernet_key()
        if not key:
            if self.AUTO_GENERATE_FERNET_KEY:
                key = Fernet.generate_key().decode("utf-8")
                self._runtime_fernet_key = key
                logger.warning(
                    "AUTO_GENERATE_FERNET_KEY is enabled, generated ephemeral key. "
                    "Persist this key to FERNET_KEY/ENCRYPTION_KEY or previously encrypted "
                    "credentials will fail after restart."
                )
                return key
            raise ValueError(_MISSING_FERNET_KEY_MESSAGE)

        self.validate_fernet_key_format(key)
        return key

settings = Settings()
