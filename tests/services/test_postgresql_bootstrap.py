"""PostgreSQL singleton location bootstrap tests."""

import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import Location
from medarchive.dependencies import get_pg_database
from medarchive.services.postgresql_bootstrap import ensure_system_locations


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_system_locations_bootstrap_is_idempotent():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    database = await get_pg_database()
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with session.begin():
            await ensure_system_locations(session)
        async with session.begin():
            await ensure_system_locations(session)

        locations = list((await session.scalars(
            select(Location).where(Location.kind.in_(("central_office", "archive")))
        )).all())
        assert {location.kind for location in locations} == {"central_office", "archive"}
        assert len(locations) == 2
        assert all(location.is_active for location in locations)
    finally:
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
