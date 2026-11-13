"""PostgreSQL movement history integration tests."""

import os
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import DomainEvent, User
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_postgresql_history_is_available_and_paginated():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="postgres-history-integration-secret",
    ))
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
    actor = User(
        id=uuid.uuid4(),
        username=f"history-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash",
        full_name="History Integration",
        role="viewer",
        is_active=True,
    )
    try:
        async with session.begin():
            session.add(actor)
            await session.flush()
            session.add_all([
                DomainEvent(
                    event_type="box.written_off",
                    aggregate_type="box",
                    aggregate_id=uuid.uuid4(),
                    actor_user_id=actor.id,
                    payload={"box_number": f"BX-HISTORY-{index}"},
                )
                for index in range(51)
            ])

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("session", token)
            first_page = await client.get("/history")
            client.cookies.set("session", token)
            second_page = await client.get("/history?page=2")
            client.cookies.set("session", token)
            dashboard = await client.get("/dashboard")

        assert first_page.status_code == 200
        assert "Журнал движений" in first_page.text
        assert "Коробка списана" in first_page.text
        assert "BX-HISTORY-50" in first_page.text
        assert "BX-HISTORY-0" not in first_page.text
        assert "Всего событий:" in first_page.text
        assert second_page.status_code == 200
        assert "BX-HISTORY-0" in second_page.text
        assert "BX-HISTORY-50" not in second_page.text
        assert dashboard.status_code == 200
        assert "/history" in dashboard.text
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
