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
    for table in ("shipments", "domain_events"):
        assert await pg_runtime_transaction.scalar(text(
            f"SELECT count(*) FROM public.{table}"
        )) == 0


@pytest.mark.asyncio
async def test_independent_sessions_and_uncommitted_visibility(pg_runtime_pair):
    first, second = pg_runtime_pair
    assert await first.scalar(text("SELECT pg_backend_pid()")) != await second.scalar(text("SELECT pg_backend_pid()"))
    await first.rollback()
    await second.rollback()
    try:
        await first.execute(text(
            "INSERT INTO public.i01_fixture_probe(id, payload) VALUES (2, 'synthetic-concurrent')"
        ))
        assert await first.scalar(text("SELECT count(*) FROM public.i01_fixture_probe WHERE id=2")) == 1
        assert await second.scalar(text("SELECT count(*) FROM public.i01_fixture_probe WHERE id=2")) == 0
    finally:
        await first.rollback()
        await second.rollback()
    assert await second.scalar(text("SELECT count(*) FROM public.i01_fixture_probe WHERE id=2")) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_conflicting_insert_and_committed_visibility(pg_runtime_pair, reverse):
    import asyncio
    from sqlalchemy.exc import DBAPIError

    first, second = pg_runtime_pair
    if reverse:
        first, second = second, first
    key = 31 if reverse else 30
    insert = text("INSERT INTO public.i01_fixture_probe(id, payload) VALUES (:id, :payload)")
    try:
        await first.execute(insert, {"id": key, "payload": "synthetic-winner"})
        await second.execute(text("SET LOCAL lock_timeout = '250ms'"))
        with pytest.raises(DBAPIError) as error:
            await asyncio.wait_for(second.execute(insert, {"id": key, "payload": "synthetic-loser"}), 5)
        assert error.value.orig.sqlstate == "55P03"
        await second.rollback()
        await first.commit()
        assert await second.scalar(text(
            "SELECT payload FROM public.i01_fixture_probe WHERE id=:id"
        ), {"id": key}) == "synthetic-winner"
        with pytest.raises(DBAPIError) as duplicate:
            await asyncio.wait_for(second.execute(insert, {"id": key, "payload": "synthetic-loser"}), 5)
        assert duplicate.value.orig.sqlstate == "23505"
    finally:
        await first.rollback()
        await second.rollback()
        await first.execute(text(
            "DELETE FROM public.i01_fixture_probe WHERE id=:id AND payload='synthetic-winner'"
        ), {"id": key})
        await first.commit()


@pytest.mark.asyncio
async def test_synthetic_card_relationships(pg_runtime_transaction):
    row = (await pg_runtime_transaction.execute(text("""
        SELECT c.card_number, c.full_name, c.phone, c.status, b.code,
               l.kind, u.is_active, u.role
        FROM public.cards c
        JOIN public.branches b ON b.id=c.origin_branch_id
        JOIN public.locations l ON l.id=c.current_location_id AND l.branch_id=b.id
        JOIN public.users u ON u.id=c.created_by AND u.branch_id=b.id
        WHERE c.card_number='I01-SYNTH-001'
    """))).one()
    assert tuple(row) == ("I01-SYNTH-001", "Synthetic patient 001", None,
                          "active", "I01-SYNTH", "branch", False, "branch")


@pytest.mark.asyncio
async def test_synthetic_reference_entities(pg_runtime_transaction):
    roles = (await pg_runtime_transaction.execute(text(
        "SELECT role FROM public.users WHERE username LIKE 'i01-synthetic-%' AND NOT is_active"
    ))).scalars().all()
    assert sorted(roles) == ["admin", "archive", "branch", "central_office"]
    kinds = (await pg_runtime_transaction.execute(text(
        "SELECT kind FROM public.locations WHERE name LIKE 'Synthetic %'"
    ))).scalars().all()
    assert sorted(kinds) == ["archive", "branch", "central_office"]
    assert await pg_runtime_transaction.scalar(text(
        "SELECT count(*) FROM public.couriers WHERE full_name='Synthetic courier' AND phone IS NULL"
    )) == 1
    assert await pg_runtime_transaction.scalar(text("""
        SELECT count(*) FROM public.boxes b JOIN public.locations l ON l.id=b.current_location_id
        WHERE b.box_number='I01-SYNTH-BOX' AND l.kind='branch'
    """)) == 1
