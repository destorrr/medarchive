import pytest
import tempfile
import os
from medarchive.config import Config


@pytest.fixture
def test_config():
    """Временная БД для тестов."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="medarchive_test_")
    os.close(fd)
    os.environ["MEDARCHIVE_DB_PATH"] = path
    os.environ["MEDARCHIVE_SECRET_KEY"] = "test-secret-key-for-testing"
    cfg = Config()
    yield cfg
    os.environ.pop("MEDARCHIVE_DB_PATH", None)
    os.environ.pop("MEDARCHIVE_SECRET_KEY", None)
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
async def db(test_config):
    from medarchive.adapters.sqlite.database import Database
    db = Database(test_config)
    await db.init()
    yield db
    await db.close()
