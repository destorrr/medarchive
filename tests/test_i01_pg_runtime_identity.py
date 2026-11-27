"""Real PostgreSQL identity tests, explicitly enabled in dedicated CI."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from tests.pg_fixture_guard import verify_test_connection

pytestmark = pytest.mark.skipif(
    os.environ.get("MEDARCHIVE_TEST_PG_ENABLED") != "1",
    reason="Dedicated PostgreSQL fixture environment required",
)


@pytest.mark.asyncio
async def test_runtime_identity(pg_runtime_connection):
    assert (await pg_runtime_connection.scalar(text("SELECT current_user"))) == "medarchive_runtime"


@pytest.mark.asyncio
@pytest.mark.parametrize("role,password", [
    ("postgres", "ci-admin-only"),
    ("medarchive_migrator", "ci-migrator-only"),
    ("medarchive_backup", "ci-backup-only"),
])
async def test_other_identity_rejected(role, password):
    url = make_url(os.environ["MEDARCHIVE_TEST_PG_URL"]).set(username=role, password=password)
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            with pytest.raises(ValueError, match="Unexpected PostgreSQL test connection identity"):
                await verify_test_connection(connection, database=url.database)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wrong_database_rejected(pg_runtime_connection):
    with pytest.raises(ValueError, match="Unexpected PostgreSQL test connection identity"):
        await verify_test_connection(pg_runtime_connection, database="medarchive_test_other")


@pytest.mark.asyncio
async def test_rollback_fixture_writes_synthetic_probe(pg_runtime_transaction):
    await pg_runtime_transaction.execute(text(
        "INSERT INTO public.i01_fixture_probe(id, payload) VALUES (1, 'synthetic-rollback')"
    ))
    assert await pg_runtime_transaction.scalar(text(
        "SELECT payload FROM public.i01_fixture_probe WHERE id=1"
    )) == "synthetic-rollback"


@pytest.mark.asyncio
async def test_rollback_fixture_leaves_no_rows(pg_runtime_transaction):
    assert await pg_runtime_transaction.scalar(text(
        "SELECT count(*) FROM public.i01_fixture_probe"
    )) == 0


@pytest.mark.asyncio
async def test_fixture_reads_migrated_application_schema(pg_runtime_transaction):
    for table in ("cards", "shipments", "domain_events"):
        assert await pg_runtime_transaction.scalar(text(
            f"SELECT count(*) FROM public.{table}"
        )) == 0
