"""Конфигурация приложения."""
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    db_path: str = field(
        default_factory=lambda: os.environ.get("MEDARCHIVE_DB_PATH", "medarchive.db")
    )
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "MEDARCHIVE_DATABASE_URL",
            "postgresql+asyncpg://medarchive:medarchive@localhost:5432/medarchive",
        )
    )
    postgres_enabled: bool = field(
        default_factory=lambda: os.environ.get("MEDARCHIVE_POSTGRES_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    auth_backend: str = field(
        default_factory=lambda: os.environ.get("MEDARCHIVE_AUTH_BACKEND", "sqlite").strip().lower()
    )
    environment: str = field(
        default_factory=lambda: os.environ.get("MEDARCHIVE_ENVIRONMENT", "development").strip().lower()
    )
    cookie_secure: bool = field(
        default_factory=lambda: os.environ.get("MEDARCHIVE_COOKIE_SECURE", "false").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    secret_key: str | None = field(
        default_factory=lambda: os.environ.get("MEDARCHIVE_SECRET_KEY")
    )

    def __post_init__(self) -> None:
        if self.auth_backend not in {"sqlite", "postgresql"}:
            raise ValueError("MEDARCHIVE_AUTH_BACKEND must be sqlite or postgresql")
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("MEDARCHIVE_ENVIRONMENT must be development, test or production")
        if self.auth_backend == "postgresql" and not self.postgres_enabled:
            raise ValueError(
                "MEDARCHIVE_POSTGRES_ENABLED=true is required for PostgreSQL authentication"
            )
        if self.environment == "production" and self.auth_backend == "postgresql":
            if not self.cookie_secure:
                raise ValueError(
                    "MEDARCHIVE_COOKIE_SECURE=true is required for PostgreSQL production authentication"
                )
            if self.secret_key is None or len(self.secret_key) < 32:
                raise ValueError(
                    "MEDARCHIVE_SECRET_KEY must contain at least 32 characters for PostgreSQL production authentication"
                )

    @property
    def effective_database_url(self) -> str:
        if not self.database_url.startswith("postgresql+asyncpg://"):
            raise RuntimeError(
                "MEDARCHIVE_DATABASE_URL must use postgresql+asyncpg://"
            )
        return self.database_url

    @property
    def effective_secret_key(self) -> str:
        if self.secret_key is None:
            raise RuntimeError(
                "MEDARCHIVE_SECRET_KEY not set — "
                "generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        return self.secret_key
