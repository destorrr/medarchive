"""Конфигурация приложения."""
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    db_path: str = field(
        default_factory=lambda: os.environ.get("MEDARCHIVE_DB_PATH", "medarchive.db")
    )
    secret_key: str | None = field(
        default_factory=lambda: os.environ.get("MEDARCHIVE_SECRET_KEY")
    )

    @property
    def effective_secret_key(self) -> str:
        if self.secret_key is None:
            raise RuntimeError(
                "MEDARCHIVE_SECRET_KEY not set — "
                "generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        return self.secret_key
