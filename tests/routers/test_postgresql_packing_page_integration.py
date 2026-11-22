"""PostgreSQL packing page UI integration coverage."""

import os
import uuid

import httpx
import pytest

from medarchive.adapters.postgresql.models import Branch, Location, User
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database
from medarchive.main import create_app

DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_postgresql_packing_page_renders_redesigned_empty_state():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="postgres-packing-page-integration-secret",
    ))
    database = await get_pg_database()
    branch_id = uuid.uuid4()
    branch = Branch(
        id=branch_id,
        code=f"PK-{uuid.uuid4().hex[:10]}",
        name="Packing Page Integration Branch",
        is_active=True,
    )
    location = Location(
        id=uuid.uuid4(), kind="branch", branch_id=branch_id,
        name=branch.name, is_active=True,
    )
    actor = User(
        id=uuid.uuid4(), username=f"packing-page-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash", full_name="Packing Page Operator",
        role="branch", branch_id=branch_id, is_active=True,
    )
    try:
        async with database.session_factory() as session:
            async with session.begin():
                session.add_all([branch, location, actor])

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            token = get_auth().create_session_token(actor)
            response = await client.get("/packing", cookies={"session": token})
            assert response.status_code == 200
            assert "Упаковка коробки" in response.text
            assert 'class="card empty-operation"' in response.text
            assert "Начните с новой коробки" in response.text
            assert 'action="/packing/new-box"' in response.text
            assert 'href="/search"' in response.text
            assert 'href="/history"' in response.text
    finally:
        async with database.session_factory() as session:
            await session.execute(
                Location.__table__.delete().where(Location.branch_id == branch_id)
            )
            persisted_actor = await session.get(User, actor.id)
            if persisted_actor is not None:
                await session.delete(persisted_actor)
            await session.flush()
            persisted_branch = await session.get(Branch, branch_id)
            if persisted_branch is not None:
                await session.delete(persisted_branch)
            await session.commit()
        await database.close()
