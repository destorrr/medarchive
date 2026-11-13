"""Role-matrix regression tests for PostgreSQL administration pages."""
import os
import uuid

import httpx
import pytest
from sqlalchemy import select

from medarchive.adapters.postgresql.models import Branch, Location, User
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database
from medarchive.main import create_app
from medarchive.services.postgresql_admin_service import PostgreSQLAdminService


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_non_admin_roles_cannot_open_postgresql_admin_pages_or_api():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="integration-test-secret",
    ))
    database = await get_pg_database()
    branch_id = None
    user_ids = []
    try:
        tokens = {}
        async with database.session_factory() as session:
            async with session.begin():
                service = PostgreSQLAdminService(session)
                branch = await service.create_branch(
                    code=f"ACL-{uuid.uuid4().hex[:10]}",
                    name="Admin ACL test branch",
                )
                branch_id = branch.id
                for role in ("branch", "central_office", "archive", "viewer"):
                    user = await service.create_user(
                        username=f"admin-acl-{role}-{uuid.uuid4().hex[:8]}",
                        full_name=f"Admin ACL {role}",
                        password_hash="test-only-hash",
                        role=role,
                        branch_id=branch_id if role == "branch" else None,
                    )
                    user_ids.append(user.id)
                    tokens[role] = get_auth().create_session_token(user)

        html_paths = (
            "/admin", "/admin/branches", "/admin/users",
            "/admin/couriers", "/admin/sections",
        )
        api_paths = ("/api/admin/branches", "/api/admin/users")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for role, token in tokens.items():
                client.cookies.set("session", token)
                for path in html_paths:
                    response = await client.get(path)
                    assert response.status_code == 403, (role, path)
                for path in api_paths:
                    response = await client.get(path)
                    assert response.status_code == 403, (role, path)
                response = await client.post(
                    "/admin/couriers",
                    data={"full_name": f"Forbidden courier {role}"},
                )
                assert response.status_code == 403, role
    finally:
        async with database.session_factory() as session:
            for user_id in user_ids:
                user = await session.get(User, user_id)
                if user is not None:
                    await session.delete(user)
            await session.flush()
            if branch_id is not None:
                location = await session.scalar(select(Location).where(
                    Location.kind == "branch", Location.branch_id == branch_id
                ))
                if location is not None:
                    await session.delete(location)
                    await session.flush()
                branch = await session.get(Branch, branch_id)
                if branch is not None:
                    await session.delete(branch)
            await session.commit()
        await database.close()
