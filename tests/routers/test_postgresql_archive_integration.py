"""PostgreSQL archive UI route smoke tests."""

import os
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import Location, User
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_postgresql_archive_pages_render_with_archive_role():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(
        Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="postgres-archive-pages-integration-secret",
        )
    )
    database = await get_pg_database()
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    async def session_override():
        yield session

    app.dependency_overrides[get_pg_session] = session_override
    archive_location = Location(
        id=uuid.uuid4(),
        kind="archive",
        branch_id=None,
        name="Archive UI Integration",
        is_active=True,
    )
    actor = User(
        id=uuid.uuid4(),
        username=f"archive-ui-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash",
        full_name="Archive UI Integration",
        role="archive",
        branch_id=None,
        is_active=True,
    )
    try:
        async with session.begin():
            session.add_all([archive_location, actor])

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("session", token)
            receive = await client.get("/receive")
            client.cookies.set("session", token)
            placement = await client.get("/placement")
            client.cookies.set("session", token)
            requests = await client.get("/requests")

        assert receive.status_code == 200
        assert "Приёмка отправлений" in receive.text
        assert placement.status_code == 200
        assert "Размещение в архиве" in placement.text
        assert requests.status_code == 200
        assert "Заявки центрального офиса" in requests.text
        assert "Archive UI Integration" in receive.text
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
