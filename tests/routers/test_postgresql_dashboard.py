"""Integration coverage for the PostgreSQL dashboard surface."""

import os
import uuid

import httpx
import pytest

from medarchive.adapters.postgresql.models import User
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database
from medarchive.main import create_app


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_postgresql_dashboard_uses_postgresql_session_and_authentication():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(
        Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="postgres-dashboard-integration-secret",
        )
    )
    database = await get_pg_database()
    actor = User(
        id=uuid.uuid4(),
        username=f"dashboard-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash",
        full_name="Dashboard Integration",
        role="viewer",
        is_active=True,
    )
    try:
        async with database.session_factory() as session:
            async with session.begin():
                session.add(actor)

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("session", token)
            response = await client.get("/dashboard")
            client.cookies.set("session", token)
            search_response = await client.get("/search")

        assert search_response.status_code == 200
        assert "Поиск по архиву" in search_response.text
        assert response.status_code == 200
        assert "Панель управления" in response.text
        assert "Карт всего" in response.text
        assert "Dashboard Integration" in response.text
        assert "/packing" not in response.text
    finally:
        async with database.session_factory() as session:
            existing = await session.get(User, actor.id)
            if existing is not None:
                await session.delete(existing)
                await session.commit()
        await database.close()
