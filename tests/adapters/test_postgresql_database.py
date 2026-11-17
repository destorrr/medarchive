import pytest

from medarchive.adapters.postgresql.database import Database


def test_postgresql_adapter_rejects_non_postgresql_url():
    with pytest.raises(ValueError, match="postgresql\\+asyncpg"):
        Database("sqlite+aiosqlite:///not-supported.db")


@pytest.mark.asyncio
async def test_postgresql_adapter_builds_async_engine_without_connecting():
    db = Database("postgresql+asyncpg://user:pass@localhost:5432/medarchive")
    try:
        assert db.engine.dialect.name == "postgresql"
        assert db.engine.url.drivername == "postgresql+asyncpg"
        assert db.session_factory.kw["expire_on_commit"] is False
    finally:
        await db.close()
