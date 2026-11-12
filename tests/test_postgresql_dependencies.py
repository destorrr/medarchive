import pytest

from medarchive.config import Config
from medarchive.dependencies import get_pg_database, get_pg_session, init_dependencies


@pytest.mark.asyncio
async def test_postgresql_dependency_is_available_when_enabled():
    config = Config(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/medarchive",
        postgres_enabled=True,
        secret_key="test-secret",
    )
    init_dependencies(config)

    database = await get_pg_database()
    assert database.engine.dialect.name == "postgresql"

    session_generator = get_pg_session()
    session = await anext(session_generator)
    try:
        assert session.bind.dialect.name == "postgresql"
    finally:
        await session_generator.aclose()
        await database.close()


@pytest.mark.asyncio
async def test_postgresql_dependency_is_disabled_by_default():
    config = Config(secret_key="test-secret")
    init_dependencies(config)

    with pytest.raises(RuntimeError, match="MEDARCHIVE_POSTGRES_ENABLED"):
        await get_pg_database()
