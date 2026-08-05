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

    def test_from_env(self):
        """Переменные окружения переопределяют дефолты."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            os.environ["MEDARCHIVE_DB_PATH"] = db_path
            os.environ["MEDARCHIVE_SECRET_KEY"] = "test-secret-42"
            cfg = Config()
            assert cfg.db_path == db_path
            assert cfg.secret_key == "test-secret-42"
        finally:
            os.unlink(db_path)
            os.environ.pop("MEDARCHIVE_DB_PATH", None)
            os.environ.pop("MEDARCHIVE_SECRET_KEY", None)
