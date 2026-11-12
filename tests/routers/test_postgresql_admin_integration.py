"""End-to-end PostgreSQL admin UI tests.

These tests run only when MEDARCHIVE_DATABASE_URL points at the disposable
PostgreSQL service started by CI. Each test removes its own rows afterwards.
"""
import os
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from medarchive.adapters.postgresql.models import Branch, Location, User
from medarchive.config import Config
from medarchive.dependencies import get_pg_database
from medarchive.main import create_app
from medarchive.routers.postgresql_admin_pages import admin_only
from medarchive.services.postgresql_admin_service import PostgreSQLAdminService


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_admin_ui_branch_employee_block_and_restore_on_postgresql():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(
        Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="integration-test-secret",
        )
    )
    database = await get_pg_database()
    actor = User(
        id=uuid.uuid4(),
        username=f"admin-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash",
        full_name="Integration Admin",
        role="admin",
        is_active=True,
    )

    async def admin_override():
        return actor

    async def session_override():
        async with database.session_factory() as session:
            yield session

    app.dependency_overrides[admin_only] = admin_override
    from medarchive.dependencies import get_pg_session
    app.dependency_overrides[get_pg_session] = session_override

    branch_code = f"IT-{uuid.uuid4().hex[:12]}"
    username = f"it-{uuid.uuid4().hex[:16]}"
    branch_id = None
    user_id = None

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Create a branch through the same HTML form used by an administrator.
            response = await client.post(
                "/admin/branches",
                data={"code": branch_code, "name": "Интеграционный филиал"},
            )
            assert response.status_code == 303
            async with database.session_factory() as session:
                branch = await session.scalar(
                    select(Branch).where(Branch.code == branch_code)
                )
                assert branch is not None
                branch_id = branch.id
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch", Location.branch_id == branch.id
                    )
                )
                assert location is not None
                assert location.name == branch.name
                assert location.is_active is True

            # Create an employee, assigning the branch and the branch role.
            response = await client.post(
                "/admin/users",
                data={
                    "username": username,
                    "full_name": "Тестовый Сотрудник",
                    "password": "Integration-Password-2026",
                    "role": "branch",
                    "branch_id": str(branch_id),
                },
            )
            assert response.status_code == 303
            async with database.session_factory() as session:
                employee = await session.scalar(
                    select(User).where(User.username == username)
                )
                assert employee is not None
                user_id = employee.id
                assert employee.role == "branch"
                assert employee.branch_id == branch_id
                assert employee.is_active is True
                assert employee.password_hash != "Integration-Password-2026"
                assert employee.password_hash

            # Block the account by submitting the edit form without its checkbox.
            response = await client.post(
                f"/admin/users/{user_id}/edit",
                data={
                    "full_name": "Тестовый Сотрудник",
                    "role": "branch",
                    "branch_id": str(branch_id),
                },
            )
            assert response.status_code == 303
            async with database.session_factory() as session:
                employee = await session.get(User, user_id)
                assert employee is not None and employee.is_active is False

            # Restore the same account.
            response = await client.post(
                f"/admin/users/{user_id}/edit",
                data={
                    "full_name": "Тестовый Сотрудник",
                    "role": "branch",
                    "branch_id": str(branch_id),
                    "is_active": "true",
                },
            )
            assert response.status_code == 303
            async with database.session_factory() as session:
                employee = await session.get(User, user_id)
                assert employee is not None and employee.is_active is True

            # An active employee prevents branch deactivation.
            response = await client.post(
                f"/admin/branches/{branch_id}/status",
                data={"is_active": "false"},
            )
            assert response.status_code == 409

            # Once the employee is blocked, the branch can be deactivated.
            response = await client.post(
                f"/admin/users/{user_id}/edit",
                data={
                    "full_name": "Тестовый Сотрудник",
                    "role": "branch",
                    "branch_id": str(branch_id),
                },
            )
            assert response.status_code == 303
            response = await client.post(
                f"/admin/branches/{branch_id}/status",
                data={"is_active": "false"},
            )
            assert response.status_code == 303

            async with database.session_factory() as session:
                branch = await session.get(Branch, branch_id)
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch", Location.branch_id == branch_id
                    )
                )
                employee = await session.get(User, user_id)
                assert branch is not None and branch.is_active is False
                assert location is not None and location.is_active is False
                assert employee is not None and employee.is_active is False
    finally:
        # Delete only records created by this test, even after an assertion fails.
        async with database.session_factory() as session:
            if user_id is not None:
                employee = await session.get(User, user_id)
                if employee is not None:
                    await session.delete(employee)
            if branch_id is not None:
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch", Location.branch_id == branch_id
                    )
                )
                if location is not None:
                    await session.delete(location)
                branch = await session.get(Branch, branch_id)
                if branch is not None:
                    await session.delete(branch)
            await session.commit()
        app.dependency_overrides.clear()
        await database.close()


@pytest.mark.asyncio
async def test_admin_service_rejects_deactivating_branch_with_active_employee():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database

    database = Database(DATABASE_URL)
    branch_id = None
    user_id = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                branch = await PostgreSQLAdminService(session).create_branch(
                    code=f"IT-{uuid.uuid4().hex[:12]}", name="Guard test branch"
                )
                branch_id = branch.id
                employee = await PostgreSQLAdminService(session).create_user(
                    username=f"guard-{uuid.uuid4().hex[:16]}",
                    full_name="Guard Test Employee",
                    password_hash="test-hash",
                    role="branch",
                    branch_id=branch.id,
                )
                user_id = employee.id
        async with database.session_factory() as session:
            with pytest.raises(ValueError, match="Сначала отключите пользователей"):
                await PostgreSQLAdminService(session).set_branch_active(branch_id, False)
            await session.rollback()
    finally:
        async with database.session_factory() as session:
            if user_id is not None:
                employee = await session.get(User, user_id)
                if employee is not None:
                    await session.delete(employee)
            if branch_id is not None:
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch", Location.branch_id == branch_id
                    )
                )
                if location is not None:
                    await session.delete(location)
                branch = await session.get(Branch, branch_id)
                if branch is not None:
                    await session.delete(branch)
            await session.commit()
        await database.close()


@pytest.mark.asyncio
async def test_admin_service_preserves_password_hash_and_requires_another_admin():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database

    database = Database(DATABASE_URL)
    admin_id = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                admin = User(
                    username=f"only-admin-{uuid.uuid4().hex[:12]}",
                    password_hash="existing-hash",
                    full_name="Only Admin",
                    role="admin",
                    is_active=True,
                )
                session.add(admin)
                await session.flush()
                admin_id = admin.id
        async with database.session_factory() as session:
            service = PostgreSQLAdminService(session)
            updated = await service.update_user(
                admin_id,
                full_name="Only Admin Updated",
                role="admin",
                branch_id=None,
                password_hash=None,
            )
            assert updated.password_hash == "existing-hash"
            await session.commit()
        async with database.session_factory() as session:
            with pytest.raises(ValueError, match="последнего активного администратора"):
                await PostgreSQLAdminService(session).update_user(
                    admin_id,
                    full_name="Only Admin Updated",
                    role="viewer",
                    branch_id=None,
                )
            await session.rollback()
    finally:
        async with database.session_factory() as session:
            if admin_id is not None:
                admin = await session.get(User, admin_id)
                if admin is not None:
                    await session.delete(admin)
                    await session.commit()
        await database.close()
