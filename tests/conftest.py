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


@pytest.fixture
async def pg_runtime_connection():
    """Opt-in isolated connection; no destructive cleanup or implicit fallback."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from tests.pg_fixture_guard import validate_test_url, verify_test_connection

    database = os.environ.get("MEDARCHIVE_TEST_PG_DATABASE")
    url = validate_test_url(
        os.environ.get("MEDARCHIVE_TEST_PG_URL", ""),
        enabled=os.environ.get("MEDARCHIVE_TEST_PG_ENABLED"),
        database=database,
        role="medarchive_runtime",
    )
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await verify_test_connection(connection, database=database)
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
async def pg_runtime_transaction(pg_runtime_connection):
    """Rollback-only fixture for tests not requiring committed state."""
    connection = pg_runtime_connection
    # The identity SELECT starts an implicit transaction.
    await connection.rollback()
    transaction = await connection.begin()
    try:
        yield connection
    finally:
        if not transaction.is_active:
            raise AssertionError("Rollback-only fixture transaction ended by test")
        await transaction.rollback()
