"""Async SQLAlchemy engine and session management for PostgreSQL."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class Database:
    """Owns the async connection pool and unit-of-work sessions."""

    def __init__(self, database_url: str, *, pool_size: int = 5, max_overflow: int = 10):
        if not database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("PostgreSQL adapter requires postgresql+asyncpg:// URL")
        self.engine = create_async_engine(
            database_url, pool_pre_ping=True, pool_size=pool_size, max_overflow=max_overflow
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )

    async def health_check(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """One atomic unit of work; exceptions trigger rollback."""
        async with self.session_factory() as session:
            async with session.begin():
                yield session

    async def close(self) -> None:
        await self.engine.dispose()
