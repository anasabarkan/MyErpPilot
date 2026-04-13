"""
Centralised application settings.

All values are loaded from environment variables / .env file.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── PostgreSQL ──────────────────────────────
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "erp_pilot"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432

    # ── OpenAI ──────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # ── JWT ─────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ── Google OAuth2 ───────────────────────────
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"
    FRONTEND_GOOGLE_CALLBACK_URL: str = "http://localhost:5173/auth/google/callback"
    BACKEND_CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    ADMIN_SEED_EMAIL: str = "admin@myerppilot.app"
    ADMIN_SEED_PASSWORD: str = "Admin12345!"
    ADMIN_SEED_FULL_NAME: str = "System Administrator"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def backend_cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.BACKEND_CORS_ORIGINS.split(",") if origin.strip()]

    class Config:
        env_file = ".env"


settings = Settings()
