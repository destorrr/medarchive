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
    archive_actor = User(
        id=uuid.uuid4(), username=f"archive-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash", full_name="Archive Operator",
        role="archive", is_active=True,
    )
    central_actor = User(
        id=uuid.uuid4(), username=f"central-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash", full_name="Central Operator",
        role="central_office", is_active=True,
    )
    viewer = User(
        id=uuid.uuid4(), username=f"viewer-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash", full_name="Read Only Viewer",
        role="viewer", is_active=True,
    )
    try:
        async with database.session_factory() as session:
            async with session.begin():
                session.add_all([branch, location, actor, archive_actor, central_actor, viewer])

        token = get_auth().create_session_token(actor)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/transfer", cookies={"session": token})
            receive_response = await client.get("/branch/receive", cookies={"session": token})

        assert receive_response.status_code == 200
        assert "Приёмка отправлений в филиале" in receive_response.text
        assert response.status_code == 200
        assert "Передача курьеру" in response.text
        assert "Нет коробок, готовых к отправке" in response.text
        assert "Transfer Integration Branch" in response.text
        for page in (response, receive_response):
            assert 'href="/search"' in page.text
            assert 'href="/history"' in page.text

        # Operational pages are isolated by role: a branch employee must not
        # enter archive or Central Office workflows, and a read-only viewer
        # must not enter any operational workflow.
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            branch_token = get_auth().create_session_token(actor)
            archive_token = get_auth().create_session_token(archive_actor)
            central_token = get_auth().create_session_token(central_actor)
            viewer_token = get_auth().create_session_token(viewer)

            for path in ("/receive", "/placement", "/requests", "/central/receive"):
                denied = await client.get(path, cookies={"session": branch_token})
                assert denied.status_code == 403, (path, denied.status_code)

            for path in ("/transfer", "/branch/receive", "/central/receive"):
                denied = await client.get(path, cookies={"session": archive_token})
                assert denied.status_code == 403, (path, denied.status_code)

            for path in ("/transfer", "/branch/receive", "/receive", "/placement", "/requests"):
                denied = await client.get(path, cookies={"session": central_token})
                assert denied.status_code == 403, (path, denied.status_code)

            for path in ("/transfer", "/branch/receive", "/receive", "/placement", "/requests", "/central/receive"):
                denied = await client.get(path, cookies={"session": viewer_token})
                assert denied.status_code == 403, (path, denied.status_code)
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
