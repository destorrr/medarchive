"""Test-only PostgreSQL connection safety checks."""
from sqlalchemy import text
from sqlalchemy.engine import make_url

def validate_test_url(url, *, enabled, database, role):
    if enabled != "1":
        raise ValueError("Explicit PostgreSQL test opt-in required")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg":
        raise ValueError("PostgreSQL asyncpg required")
    if parsed.query:
        raise ValueError("Connection query overrides are forbidden")
    if parsed.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Loopback test server required")
    if not database or parsed.database != database or not database.startswith("medarchive_test_"):
        raise ValueError("Explicit test database allowlist required")
    if role != "medarchive_runtime" or parsed.username != role:
        raise ValueError("Dedicated runtime identity required")
    return parsed


async def verify_test_connection(connection, *, database):
    row = (await connection.execute(text("""
        SELECT current_database(), session_user, current_user,
               current_setting('server_version_num')::integer,
               (SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
                FROM pg_roles WHERE rolname = current_user)
    """))).one()
    if (row[0] != database or row[1] != "medarchive_runtime"
            or row[2] != "medarchive_runtime" or not 160000 <= row[3] < 170000
            or row[4] is not False):
        raise ValueError("Unexpected PostgreSQL test connection identity")


async def cleanup_committed_probe(connection, *, url, enabled, database, key):
    """Remove only the known committed concurrency probe in a verified test DB."""
    import asyncio

    validate_test_url(url, enabled=enabled, database=database, role="medarchive_runtime")
    if key not in (30, 31):
        raise ValueError("Unknown concurrency probe key")
    if connection.in_transaction():
        raise ValueError("Cleanup requires an idle connection")
    try:
        await asyncio.wait_for(verify_test_connection(connection, database=database), 5)
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(text("SET LOCAL lock_timeout = '1s'"))
        await asyncio.wait_for(connection.execute(text(
            "DELETE FROM public.i01_fixture_probe WHERE id=:id AND payload='synthetic-winner'"
        ), {"id": key}), 5)
        await asyncio.wait_for(connection.commit(), 5)
    except BaseException:
        await connection.rollback()
        raise
