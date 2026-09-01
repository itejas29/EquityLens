from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"

    database_url: str
    redis_url: str

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, v: str) -> str:
        """Managed Postgres providers (Neon, Render, old-style Heroku URLs)
        hand out `postgres://` or a bare `postgresql://` with no driver suffix.
        SQLAlchemy rejects the `postgres://` scheme outright — this only
        matters once the app leaves docker-compose, where DATABASE_URL is
        written by hand with the +psycopg2 suffix already in place."""
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://") and "+psycopg2" not in v:
            v = v.replace("postgresql://", "postgresql+psycopg2://", 1)
        return v

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # Both ports, because the frontend is served from either depending on how
    # you run it: 5173 is the Vite dev server, 3000 is the nginx container in
    # docker-compose. Defaulting to 5173 alone meant every Docker deployment
    # rejected its own frontend's preflight and the browser reported it as an
    # opaque "Network Error". Matches .env.example.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    # Optional — AI trading activity notifications (app/services/notifications.py).
    # Notifications are silently skipped when either is unset, so this is opt-in
    # and can never break the trading loop for a deployment that hasn't set them.
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None


settings = Settings()
