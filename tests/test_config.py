# tests/test_config.py
import os
import tempfile
from medarchive.config import Config


class TestConfig:
    def test_defaults(self):
        """Конфиг без переменных окружения имеет все поля с разумными дефолтами."""
        cfg = Config()
        assert cfg.db_path == "medarchive.db"
        assert cfg.secret_key is None
        assert cfg.database_url.startswith("postgresql+asyncpg://")
        assert cfg.effective_database_url == cfg.database_url
        assert cfg.postgres_enabled is False

    def test_from_env(self, monkeypatch):
        """Переменные окружения переопределяют дефолты."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            monkeypatch.setenv("MEDARCHIVE_DB_PATH", db_path)
            monkeypatch.setenv("MEDARCHIVE_SECRET_KEY", "test-secret-42")
            monkeypatch.setenv("MEDARCHIVE_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
            monkeypatch.setenv("MEDARCHIVE_POSTGRES_ENABLED", "true")
            cfg = Config()
            assert cfg.db_path == db_path
            assert cfg.secret_key == "test-secret-42"
            assert cfg.effective_database_url == "postgresql+asyncpg://test:test@localhost/test"
            assert cfg.postgres_enabled is True
        finally:
            os.unlink(db_path)
