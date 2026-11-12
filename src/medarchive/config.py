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
        default_factory=lambda: os.environ.get("MEDARCHIVE_POSTGRES_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    )
    secret_key: str | None = field(
        default_factory=lambda: os.environ.get("MEDARCHIVE_SECRET_KEY")
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
