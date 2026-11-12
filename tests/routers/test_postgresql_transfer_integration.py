"""PostgreSQL transfer page route coverage."""

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
async def test_postgresql_branch_transfer_page_is_scoped_to_branch():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(
        Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="postgres-transfer-integration-secret",
        )
    )
    database = await get_pg_database()
    branch_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    branch = Branch(
        id=branch_id,
        code=f"TR-{uuid.uuid4().hex[:10]}",
        name="Transfer Integration Branch",
        is_active=True,
    )
    location = Location(
        id=uuid.uuid4(),
        kind="branch",
        branch_id=branch_id,
        name=branch.name,
        is_active=True,
    )
    actor = User(
        id=actor_id,
        username=f"transfer-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash",
        full_name="Transfer Integration",
        role="branch",
        branch_id=branch_id,
        is_active=True,
    )
    try:
        async with database.session_factory() as session:
            async with session.begin():
                session.add_all([branch, location, actor])

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/transfer", cookies={"session": token})

        assert response.status_code == 200
        assert "Передача курьеру" in response.text
        assert "Нет коробок, готовых к отправке" in response.text
        assert "Transfer Integration Branch" in response.text
    finally:
        async with database.session_factory() as session:
            await session.execute(
                Location.__table__.delete().where(Location.branch_id == branch_id)
            )
            actor = await session.get(User, actor_id)
            if actor is not None:
                await session.delete(actor)
            await session.flush()
            branch = await session.get(Branch, branch_id)
            if branch is not None:
                await session.delete(branch)
            await session.commit()
        await database.close()
