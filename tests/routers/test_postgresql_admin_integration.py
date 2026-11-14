"""End-to-end PostgreSQL admin UI tests.

These tests run only when MEDARCHIVE_DATABASE_URL points at the disposable
PostgreSQL service started by CI. Each test removes its own rows afterwards.
"""
import os
import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    ArchiveShelf, Box, BoxCardMembership, BoxCycle, BoxPlacement, Branch,
    Card, CardPlacement, Courier, Location, Shipment, User,
)
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

            # A branch employee can use the dashboard but cannot call admin APIs.
            from medarchive.dependencies import get_auth
            employee_session = get_auth().create_session_token(employee)
            client.cookies.set("session", employee_session)
            response = await client.get("/dashboard")
            assert response.status_code == 200
            response = await client.get("/api/admin/branches")
            assert response.status_code == 403

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

            # Deactivation invalidates already-issued sessions immediately.
            response = await client.get("/dashboard")
            assert response.status_code == 401

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

            # Restoring the account makes the same valid session usable again.
            response = await client.get("/dashboard")
            assert response.status_code == 200

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
                    await session.flush()
            if branch_id is not None:
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch", Location.branch_id == branch_id
                    )
                )
                if location is not None:
                    await session.delete(location)
                    await session.flush()
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
                    await session.flush()
            if branch_id is not None:
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch", Location.branch_id == branch_id
                    )
                )
                if location is not None:
                    await session.delete(location)
                    await session.flush()
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


@pytest.mark.asyncio
async def test_concurrent_admin_demotion_keeps_one_active_admin():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    import asyncio
    from medarchive.adapters.postgresql.database import Database

    database = Database(DATABASE_URL)
    admin_ids = []
    try:
        async with database.session_factory() as session:
            async with session.begin():
                for index in range(2):
                    admin = User(
                        username=f"race-admin-{index}-{uuid.uuid4().hex[:10]}",
                        password_hash="test-hash",
                        full_name=f"Race Admin {index}",
                        role="admin",
                        is_active=True,
                    )
                    session.add(admin)
                    await session.flush()
                    admin_ids.append(admin.id)

        async def demote(admin_id):
            async with database.session_factory() as session:
                try:
                    async with session.begin():
                        await PostgreSQLAdminService(session).update_user(
                            admin_id,
                            full_name="Demoted Admin",
                            role="viewer",
                            branch_id=None,
                        )
                    return "updated"
                except ValueError as exc:
                    assert "последнего активного администратора" in str(exc)
                    return "rejected"

        outcomes = await asyncio.gather(*(demote(admin_id) for admin_id in admin_ids))
        assert sorted(outcomes) == ["rejected", "updated"]
        async with database.session_factory() as session:
            active_admin = await session.scalar(
                select(User).where(User.role == "admin", User.is_active.is_(True))
            )
            assert active_admin is not None
    finally:
        async with database.session_factory() as session:
            for admin_id in admin_ids:
                admin = await session.get(User, admin_id)
                if admin is not None:
                    await session.delete(admin)
            await session.commit()
        await database.close()


@pytest.mark.asyncio
async def test_courier_with_draft_shipment_cannot_be_deactivated():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database
    from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow

    database = Database(DATABASE_URL)
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with session.begin():
            branch = Branch(
                code=f"CR-{uuid.uuid4().hex[:10]}",
                name="Courier race test branch",
                is_active=True,
            )
            session.add(branch)
            await session.flush()
            source = Location(
                kind="branch", branch_id=branch.id,
                name=branch.name, is_active=True,
            )
            actor = User(
                username=f"courier-test-{uuid.uuid4().hex[:12]}",
                password_hash="test-hash",
                full_name="Courier Test Actor",
                role="branch", branch_id=branch.id, is_active=True,
            )
            courier = Courier(full_name="Courier Test", is_active=True)
            session.add_all([source, actor, courier])
            await session.flush()
            target = await session.scalar(
                select(Location).where(
                    Location.kind == "central_office", Location.is_active.is_(True)
                )
            )
            if target is None:
                target = Location(
                    kind="central_office", branch_id=None,
                    name="Test Central Office", is_active=True,
                )
                session.add(target)
                await session.flush()

            central_actor = User(
                username=f"courier-central-{uuid.uuid4().hex[:12]}",
                password_hash="test-hash",
                full_name="Courier Central Receiver",
                role="central_office",
                branch_id=None,
                is_active=True,
            )
            session.add(central_actor)
            await session.flush()

            card = Card(
                card_number=f"CR-CARD-{uuid.uuid4().hex[:10]}",
                full_name="Courier Exception Test Patient",
                date_of_birth=date(1980, 1, 1),
                origin_branch_id=branch.id,
                current_location_id=source.id,
                current_shipment_id=None,
                created_by=actor.id,
            )
            session.add(card)
            await session.flush()
            shipment = await PostgreSQLShipmentWorkflow(session).create_draft(
                from_location_id=source.id,
                to_location_id=target.id,
                courier_id=courier.id,
                issued_by=actor.id,
            )
            workflow = PostgreSQLShipmentWorkflow(session)
            await workflow.add_standalone_card(
                shipment_id=shipment.id, card_id=card.id, actor_id=actor.id
            )
            with pytest.raises(ValueError, match="активными отправлениями"):
                await PostgreSQLAdminService(session).set_courier_active(
                    courier.id, False
                )

            shipment = await session.scalar(
                select(Shipment).where(Shipment.courier_id == courier.id)
            )
            assert shipment is not None
            await workflow.hand_over(shipment_id=shipment.id, actor_id=actor.id)
            with pytest.raises(ValueError, match="активными отправлениями"):
                await PostgreSQLAdminService(session).set_courier_active(
                    courier.id, False
                )
            assert courier.is_active is True

            await workflow.register_exception(
                shipment_id=shipment.id,
                actor_id=actor.id,
                note="Тестовый инцидент: отправление ещё не принято",
            )
            with pytest.raises(ValueError, match="активными отправлениями"):
                await PostgreSQLAdminService(session).set_courier_active(
                    courier.id, False
                )

            received = await workflow.resolve_exception(
                shipment_id=shipment.id,
                actor_id=central_actor.id,
                resolution_note="Отправление полностью проверено и принято",
            )
            assert received.status == "received"
            assert card.current_location_id == target.id
            assert card.current_shipment_id is None

            await PostgreSQLAdminService(session).set_courier_active(
                courier.id, False
            )
            assert courier.is_active is False
            retained_shipment = await session.scalar(
                select(Shipment).where(Shipment.id == shipment.id)
            )
            assert retained_shipment is not None
            assert retained_shipment.status == "received"
            assert retained_shipment.courier_id == courier.id
    finally:
        await session.rollback()
        await session.close()
        await outer_transaction.rollback()
        await connection.close()
        await database.close()



@pytest.mark.asyncio
async def test_shelf_with_active_box_placement_cannot_be_deactivated():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database
    from medarchive.services.postgresql_archive_workflow import PostgreSQLArchiveWorkflow

    database = Database(DATABASE_URL)
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    shelf_id = None
    box_id = None
    actor_id = None
    created_archive_id = None
    try:
        async with session.begin():
            archive = await session.scalar(
                select(Location).where(Location.kind == "archive")
            )
            if archive is None:
                archive = Location(
                    kind="archive", branch_id=None,
                    name="Shelf placement guard archive", is_active=True,
                )
                session.add(archive)
                await session.flush()
                created_archive_id = archive.id

            actor = User(
                username=f"shelf-guard-{uuid.uuid4().hex[:12]}",
                password_hash="test-hash",
                full_name="Shelf Guard Actor",
                role="archive",
                is_active=True,
            )
            shelf = ArchiveShelf(
                section_name=f"Guard-{uuid.uuid4().hex[:8]}",
                shelf_name="Occupied shelf",
                is_active=True,
            )
            session.add_all([actor, shelf])
            await session.flush()
            actor_id, shelf_id = actor.id, shelf.id

            box = Box(
                box_number=f"SG-{uuid.uuid4().hex[:12]}",
                current_location_id=archive.id,
                current_shipment_id=None,
                status="active",
                created_by=actor.id,
            )
            session.add(box)
            await session.flush()
            box_id = box.id

            card = Card(
                card_number=f"SG-CARD-{uuid.uuid4().hex[:10]}",
                full_name="Shelf Guard Box Patient",
                date_of_birth=date(1980, 1, 1),
                origin_branch_id=uuid.uuid4(),
                current_location_id=archive.id,
                current_shipment_id=None,
                created_by=actor.id,
            )
            # Use the branch that owns a valid origin location for the card.
            origin_branch = await session.scalar(
                select(Branch).limit(1)
            )
            if origin_branch is None:
                origin_branch = Branch(
                    code=f"SG-BR-{uuid.uuid4().hex[:8]}",
                    name="Shelf Guard Origin",
                    is_active=True,
                )
                session.add(origin_branch)
                await session.flush()
                origin_location = Location(
                    kind="branch", branch_id=origin_branch.id,
                    name=origin_branch.name, is_active=True,
                )
                session.add(origin_location)
                await session.flush()
            card.origin_branch_id = origin_branch.id
            session.add(card)
            await session.flush()
            cycle = BoxCycle(
                box_id=box.id, cycle_number=1, started_by=actor.id
            )
            session.add(cycle)
            await session.flush()
            session.add(BoxCardMembership(
                box_cycle_id=cycle.id, card_id=card.id, added_by=actor.id
            ))
            await session.flush()

            await PostgreSQLArchiveWorkflow(session).place_box(
                box_id=box.id, shelf_id=shelf.id, actor_id=actor.id
            )

            with pytest.raises(ValueError, match="Сначала снимите все коробки и карты"):
                await PostgreSQLAdminService(session).set_shelf_active(
                    shelf.id, False
                )

            persisted_shelf = await session.get(ArchiveShelf, shelf.id)
            placement = await session.scalar(
                select(BoxPlacement).where(
                    BoxPlacement.box_id == box.id,
                    BoxPlacement.shelf_id == shelf.id,
                    BoxPlacement.removed_at.is_(None),
                )
            )
            assert persisted_shelf is not None and persisted_shelf.is_active is True
            assert placement is not None

            removed = await PostgreSQLArchiveWorkflow(session).remove_box_from_shelf(
                box_id=box.id, actor_id=actor.id
            )
            assert removed.removed_at is not None
            assert removed.removed_by == actor.id
            await PostgreSQLAdminService(session).set_shelf_active(
                shelf.id, False
            )
            assert shelf.is_active is False
            historical_placement = await session.get(BoxPlacement, placement.id)
            assert historical_placement is not None
            assert historical_placement.removed_at is not None
    finally:
        await session.rollback()
        await session.close()
        await outer_transaction.rollback()
        await connection.close()
        await database.close()


@pytest.mark.asyncio
async def test_admin_html_courier_shelf_forms_and_validation_on_postgresql():
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

    from medarchive.dependencies import get_pg_session
    app.dependency_overrides[admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override

    courier_name = f"HTML Courier {uuid.uuid4().hex[:8]}"
    section_name = f"HTML Section {uuid.uuid4().hex[:8]}"
    shelf_name = f"Shelf {uuid.uuid4().hex[:8]}"
    courier_id = None
    optional_phone_courier_id = None
    shelf_id = None

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Verify that all administrative list pages render for an administrator.
            for path in (
                "/admin/branches", "/admin/users",
                "/admin/couriers", "/admin/sections",
            ):
                response = await client.get(path)
                assert response.status_code == 200
                assert "text/html" in response.headers["content-type"]

            # A branch-scoped account cannot be created without selecting a branch.
            response = await client.post(
                "/admin/users",
                data={
                    "username": f"invalid-{uuid.uuid4().hex[:10]}",
                    "full_name": "Invalid Branch User",
                    "password": "Integration-Password-2026",
                    "role": "branch",
                    "branch_id": "",
                },
            )
            assert response.status_code == 422

            response = await client.post(
                "/admin/couriers",
                data={"full_name": "   ", "phone": "+7 900 000-00-00"},
            )
            assert response.status_code == 422
            async with database.session_factory() as session:
                assert await session.scalar(
                    select(Courier).where(Courier.full_name == "")
                ) is None

            response = await client.post(
                "/admin/couriers",
                data={"full_name": courier_name, "phone": "+7 900 000-00-00"},
            )
            assert response.status_code == 303
            async with database.session_factory() as session:
                courier = await session.scalar(
                    select(Courier).where(Courier.full_name == courier_name)
                )
                assert courier is not None
                courier_id = courier.id
                assert courier.phone == "+7 900 000-00-00"
                assert courier.is_active is True

            # Whitespace-only optional phone values are normalized to NULL.
            response = await client.post(
                "/admin/couriers",
                data={"full_name": f"{courier_name} optional phone", "phone": "   "},
            )
            assert response.status_code == 303
            async with database.session_factory() as session:
                optional_phone_courier = await session.scalar(
                    select(Courier).where(
                        Courier.full_name == f"{courier_name} optional phone"
                    )
                )
                assert optional_phone_courier is not None
                optional_phone_courier_id = optional_phone_courier.id
                assert optional_phone_courier.phone is None

            response = await client.get("/admin/couriers")
            assert response.status_code == 200
            assert courier_name in response.text
            assert "+7 900 000-00-00" in response.text
            assert f"{courier_name} optional phone" in response.text

            missing_courier = await client.post(
                f"/admin/couriers/{uuid.uuid4()}/status",
                data={"is_active": "false"},
                follow_redirects=False,
            )
            assert missing_courier.status_code == 404

            response = await client.post(
                f"/admin/couriers/{courier_id}/status",
                data={"is_active": "false"},
            )
            assert response.status_code == 303
            response = await client.post(
                f"/admin/couriers/{courier_id}/status",
                data={"is_active": "true"},
            )
            assert response.status_code == 303

            response = await client.post(
                "/admin/sections",
                data={"section_name": section_name, "shelf_name": shelf_name},
            )
            assert response.status_code == 303
            async with database.session_factory() as session:
                shelf = await session.scalar(
                    select(ArchiveShelf).where(
                        ArchiveShelf.section_name == section_name,
                        ArchiveShelf.shelf_name == shelf_name,
                    )
                )
                assert shelf is not None
                shelf_id = shelf.id
                assert shelf.is_active is True

            edited_section_name = f"{section_name} Updated"
            edited_shelf_name = f"{shelf_name} Updated"
            response = await client.post(
                f"/admin/sections/{shelf_id}/edit",
                data={
                    "section_name": edited_section_name,
                    "shelf_name": edited_shelf_name,
                },
            )
            assert response.status_code == 303
            async with database.session_factory() as session:
                shelf = await session.get(ArchiveShelf, shelf_id)
                assert shelf is not None
                assert shelf.section_name == edited_section_name
                assert shelf.shelf_name == edited_shelf_name

            invalid_edit = await client.post(
                f"/admin/sections/{shelf_id}/edit",
                data={"section_name": "   ", "shelf_name": edited_shelf_name},
            )
            assert invalid_edit.status_code == 422
            missing_shelf = await client.post(
                f"/admin/sections/{uuid.uuid4()}/edit",
                data={"section_name": "Missing", "shelf_name": "Missing"},
            )
            assert missing_shelf.status_code == 404

            response = await client.post(
                f"/admin/sections/{shelf_id}/status",
                data={"is_active": "false"},
            )
            assert response.status_code == 303
            response = await client.post(
                f"/admin/sections/{shelf_id}/status",
                data={"is_active": "true"},
            )
            assert response.status_code == 303

            async with database.session_factory() as session:
                courier = await session.get(Courier, courier_id)
                shelf = await session.get(ArchiveShelf, shelf_id)
                assert courier is not None and courier.is_active is True
                assert shelf is not None and shelf.is_active is True
    finally:
        async with database.session_factory() as session:
            for cleanup_courier_id in (courier_id, optional_phone_courier_id):
                if cleanup_courier_id is not None:
                    courier = await session.get(Courier, cleanup_courier_id)
                    if courier is not None:
                        await session.delete(courier)
                        await session.flush()
            if shelf_id is not None:
                shelf = await session.get(ArchiveShelf, shelf_id)
                if shelf is not None:
                    await session.delete(shelf)
            await session.commit()
        app.dependency_overrides.clear()
        await database.close()



@pytest.mark.asyncio
async def test_admin_service_rejects_deactivating_shelf_with_active_card_placement():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database

    database = Database(DATABASE_URL)
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with session.begin():
            branch = Branch(
                code=f"SH-{uuid.uuid4().hex[:10]}",
                name="Shelf guard test branch",
                is_active=True,
            )
            session.add(branch)
            await session.flush()
            branch_location = Location(
                kind="branch", branch_id=branch.id,
                name=branch.name, is_active=True,
            )
            archive_actor = User(
                username=f"shelf-guard-{uuid.uuid4().hex[:12]}",
                password_hash="test-hash",
                full_name="Shelf Guard Actor",
                role="archive",
                branch_id=None,
                is_active=True,
            )
            shelf = ArchiveShelf(
                section_name=f"Guard {uuid.uuid4().hex[:8]}",
                shelf_name="A-01",
                is_active=True,
            )
            session.add_all([branch_location, archive_actor, shelf])
            await session.flush()

            archive = await session.scalar(
                select(Location).where(Location.kind == "archive")
            )
            if archive is None:
                archive = Location(
                    kind="archive", branch_id=None,
                    name="Test Archive", is_active=True,
                )
                session.add(archive)
                await session.flush()

            card = Card(
                card_number=f"GUARD-{uuid.uuid4().hex[:12]}",
                full_name="Shelf Guard Patient",
                date_of_birth=date(1980, 1, 1),
                origin_branch_id=branch.id,
                current_location_id=archive.id,
                current_shipment_id=None,
                created_by=archive_actor.id,
            )
            session.add(card)
            await session.flush()
            session.add(CardPlacement(
                card_id=card.id,
                shelf_id=shelf.id,
                placed_by=archive_actor.id,
            ))
            await session.flush()

            with pytest.raises(ValueError, match="Сначала снимите все коробки и карты"):
                await PostgreSQLAdminService(session).set_shelf_active(
                    shelf.id, False
                )
            assert shelf.is_active is True

            from medarchive.services.postgresql_archive_workflow import PostgreSQLArchiveWorkflow
            removed = await PostgreSQLArchiveWorkflow(session).remove_card_from_shelf(
                card_id=card.id, actor_id=archive_actor.id
            )
            assert removed.removed_at is not None
            assert removed.removed_by == archive_actor.id
            await PostgreSQLAdminService(session).set_shelf_active(
                shelf.id, False
            )
            assert shelf.is_active is False
            historical_placement = await session.get(CardPlacement, removed.id)
            assert historical_placement is not None
            assert historical_placement.removed_at is not None
    finally:
        await session.rollback()
        await session.close()
        await outer_transaction.rollback()
        await connection.close()
        await database.close()

@pytest.mark.asyncio
async def test_admin_service_rejects_deactivating_branch_with_remaining_card_inventory():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database

    database = Database(DATABASE_URL)
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with session.begin():
            branch = Branch(
                code=f"INV-{uuid.uuid4().hex[:10]}",
                name="Inventory guard branch",
                is_active=True,
            )
            session.add(branch)
            await session.flush()
            location = Location(
                kind="branch",
                branch_id=branch.id,
                name=branch.name,
                is_active=True,
            )
            session.add(location)
            await session.flush()
            card = Card(
                card_number=f"INV-{uuid.uuid4().hex[:12]}",
                full_name="Inventory Guard Patient",
                date_of_birth=date(1980, 1, 1),
                origin_branch_id=branch.id,
                current_location_id=location.id,
                current_shipment_id=None,
            )
            session.add(card)
            await session.flush()

            with pytest.raises(ValueError, match="на его балансе остаются карты или коробки"):
                await PostgreSQLAdminService(session).set_branch_active(
                    branch.id, False
                )
            assert branch.is_active is True
            assert location.is_active is True
    finally:
        await session.rollback()
        await session.close()
        await outer_transaction.rollback()
        await connection.close()
        await database.close()


@pytest.mark.asyncio
async def test_qr_intake_waits_for_branch_deactivation_location_lock():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    import asyncio
    import json
    from sqlalchemy import text
    from medarchive.adapters.postgresql.database import Database
    from medarchive.services.postgresql_card_intake import (
        CardIntakeError, PostgreSQLCardIntakeService,
    )

    database = Database(DATABASE_URL)
    branch_id = None
    user_id = None
    card_number = f"QR-RACE-{uuid.uuid4().hex}"
    intake_task = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                branch = await PostgreSQLAdminService(session).create_branch(
                    code=f"RACE-{uuid.uuid4().hex[:10]}", name="QR race branch"
                )
                branch_id = branch.id
                employee = await PostgreSQLAdminService(session).create_user(
                    username=f"qr-race-{uuid.uuid4().hex[:12]}",
                    full_name="QR Race Employee",
                    password_hash="test-hash",
                    role="branch",
                    branch_id=branch.id,
                )
                user_id = employee.id

        # Keep the deactivation transaction open after it has locked and
        # marked the branch location inactive. A concurrent QR intake must wait.
        async with database.session_factory() as admin_session:
            await admin_session.begin()
            employee = await admin_session.get(User, user_id)
            employee.is_active = False
            await admin_session.flush()
            await PostgreSQLAdminService(admin_session).set_branch_active(
                branch_id, False
            )

            intake_started = asyncio.Event()
            intake_pid = {}

            async def register_card():
                async with database.session_factory() as intake_session:
                    async with intake_session.begin():
                        intake_pid["pid"] = await intake_session.scalar(
                            text("SELECT pg_backend_pid()")
                        )
                        intake_started.set()
                        return await PostgreSQLCardIntakeService(
                            intake_session
                        ).register_from_qr(
                            payload=json.dumps({
                                "version": 1,
                                "card_number": card_number,
                                "full_name": "QR Race Patient",
                                "birth_date": "1985-04-03",
                            }),
                            actor_id=user_id,
                        )

            intake_task = asyncio.create_task(register_card())
            await asyncio.wait_for(intake_started.wait(), timeout=5)

            # Confirm PostgreSQL itself reports that the intake connection is
            # blocked by the deactivation transaction before releasing the lock.
            blocked = False
            for _ in range(250):
                blockers = await admin_session.scalar(
                    text("SELECT pg_blocking_pids(:pid)"),
                    {"pid": intake_pid["pid"]},
                )
                if blockers:
                    blocked = True
                    break
                await asyncio.sleep(0.02)
            assert blocked, "QR intake did not wait for the branch location lock"

            await admin_session.commit()

        with pytest.raises(CardIntakeError, match="активное место хранения"):
            await asyncio.wait_for(intake_task, timeout=5)
        intake_task = None
    finally:
        if intake_task is not None and not intake_task.done():
            intake_task.cancel()
            await asyncio.gather(intake_task, return_exceptions=True)
        async with database.session_factory() as session:
            card = await session.scalar(
                select(Card).where(Card.card_number == card_number)
            )
            if card is not None:
                await session.delete(card)
                await session.flush()
            if user_id is not None:
                employee = await session.get(User, user_id)
                if employee is not None:
                    await session.delete(employee)
                    await session.flush()
            if branch_id is not None:
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch", Location.branch_id == branch_id
                    )
                )
                if location is not None:
                    await session.delete(location)
                    await session.flush()
                branch = await session.get(Branch, branch_id)
                if branch is not None:
                    await session.delete(branch)
            await session.commit()
        await database.close()


@pytest.mark.asyncio
async def test_branch_with_active_box_inventory_cannot_be_deactivated():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database

    database = Database(DATABASE_URL)
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with session.begin():
            branch = Branch(
                code=f"AB-{uuid.uuid4().hex[:10]}",
                name="Active box inventory branch",
                is_active=True,
            )
            session.add(branch)
            await session.flush()
            location = Location(
                kind="branch", branch_id=branch.id,
                name=branch.name, is_active=True,
            )
            actor = User(
                username=f"active-box-{uuid.uuid4().hex[:12]}",
                password_hash="test-hash",
                full_name="Active Box Test Actor",
                role="branch",
                branch_id=branch.id,
                is_active=True,
            )
            session.add_all([location, actor])
            await session.flush()
            box = Box(
                box_number=f"AB-BOX-{uuid.uuid4().hex[:10]}",
                current_location_id=location.id,
                current_shipment_id=None,
                status="active",
                created_by=actor.id,
            )
            session.add(box)
            await session.flush()

            actor.is_active = False
            await session.flush()
            with pytest.raises(
                ValueError, match="на его балансе остаются карты или коробки"
            ):
                await PostgreSQLAdminService(session).set_branch_active(
                    branch.id, False
                )
            assert branch.is_active is True
            assert location.is_active is True
            assert box.status == "active"
            assert box.current_location_id == location.id
    finally:
        await session.rollback()
        await session.close()
        await outer_transaction.rollback()
        await connection.close()
        await database.close()


@pytest.mark.asyncio
async def test_branch_can_be_deactivated_after_its_empty_box_is_written_off():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database
    from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow

    database = Database(DATABASE_URL)
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with session.begin():
            branch = Branch(
                code=f"WO-{uuid.uuid4().hex[:10]}",
                name="Written-off box branch",
                is_active=True,
            )
            session.add(branch)
            await session.flush()
            location = Location(
                kind="branch",
                branch_id=branch.id,
                name=branch.name,
                is_active=True,
            )
            actor = User(
                username=f"writeoff-{uuid.uuid4().hex[:12]}",
                password_hash="test-hash",
                full_name="Write-off Test Actor",
                role="branch",
                branch_id=branch.id,
                is_active=True,
            )
            session.add_all([location, actor])
            await session.flush()
            box = Box(
                box_number=f"WO-BOX-{uuid.uuid4().hex[:10]}",
                current_location_id=location.id,
                current_shipment_id=None,
                status="active",
                created_by=actor.id,
            )
            session.add(box)
            await session.flush()

            written_off = await PostgreSQLShipmentWorkflow(session).write_off_box(
                box_id=box.id, actor_id=actor.id
            )
            assert written_off.status == "written_off"
            assert written_off.current_location_id == location.id

            # The user must be deactivated before the branch can be disabled.
            actor.is_active = False
            await session.flush()
            await PostgreSQLAdminService(session).set_branch_active(
                branch.id, False
            )

            assert branch.is_active is False
            assert location.is_active is False
            assert written_off.status == "written_off"
    finally:
        await session.rollback()
        await session.close()
        await outer_transaction.rollback()
        await connection.close()
        await database.close()



@pytest.mark.asyncio
async def test_branch_with_unfinished_shipments_cannot_be_deactivated():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database
    from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow

    database = Database(DATABASE_URL)
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with session.begin():
            branch = Branch(
                code=f"BS-{uuid.uuid4().hex[:10]}",
                name="Branch shipment guard test",
                is_active=True,
            )
            session.add(branch)
            await session.flush()
            source = Location(
                kind="branch", branch_id=branch.id,
                name=branch.name, is_active=True,
            )
            actor = User(
                username=f"branch-shipment-{uuid.uuid4().hex[:12]}",
                password_hash="test-hash",
                full_name="Branch Shipment Test Actor",
                role="branch", branch_id=branch.id, is_active=True,
            )
            courier = Courier(full_name="Branch Shipment Test Courier", is_active=True)
            session.add_all([source, actor, courier])
            await session.flush()
            target = await session.scalar(
                select(Location).where(
                    Location.kind == "central_office", Location.is_active.is_(True)
                )
            )
            if target is None:
                target = Location(
                    kind="central_office", branch_id=None,
                    name="Test Central Office", is_active=True,
                )
                session.add(target)
                await session.flush()

            workflow = PostgreSQLShipmentWorkflow(session)
            shipment = await workflow.create_draft(
                from_location_id=source.id,
                to_location_id=target.id,
                courier_id=courier.id,
                issued_by=actor.id,
            )
            actor.is_active = False
            await session.flush()

            # An empty draft isolates the unfinished-shipment guard from the
            # separate rule that blocks deactivation while cards remain on balance.
            with pytest.raises(ValueError, match="незавершённые отправления"):
                await PostgreSQLAdminService(session).set_branch_active(
                    branch.id, False
                )
            assert branch.is_active is True
            assert source.is_active is True
            assert shipment.status == "draft"

            # Create the card only after the empty-draft guard has been checked:
            # any card on the branch balance independently blocks deactivation.
            card = Card(
                card_number=f"BS-CARD-{uuid.uuid4().hex[:10]}",
                full_name="Branch Shipment Guard Patient",
                date_of_birth=date(1980, 1, 1),
                origin_branch_id=branch.id,
                current_location_id=source.id,
                current_shipment_id=None,
                created_by=actor.id,
            )
            session.add(card)
            await session.flush()

            # Re-enable the branch actor only to perform the branch-side
            # manifest and handoff operations; disable it again before the guard check.
            actor.is_active = True
            await session.flush()
            await workflow.add_standalone_card(
                shipment_id=shipment.id, card_id=card.id, actor_id=actor.id
            )
            await workflow.hand_over(shipment_id=shipment.id, actor_id=actor.id)

            # In-transit shipments independently prevent branch deactivation.
            actor.is_active = False
            await session.flush()
            with pytest.raises(ValueError, match="незавершённые отправления"):
                await PostgreSQLAdminService(session).set_branch_active(
                    branch.id, False
                )
            assert branch.is_active is True
            assert source.is_active is True
            assert shipment.status == "in_transit"

            # Restore the actor only to register the incident, then verify the
            # same guard continues to protect exception-state shipments.
            actor.is_active = True
            await session.flush()
            await workflow.register_exception(
                shipment_id=shipment.id,
                actor_id=actor.id,
                note="Отправление ожидает разрешения инцидента",
            )
            actor.is_active = False
            await session.flush()
            with pytest.raises(ValueError, match="незавершённые отправления"):
                await PostgreSQLAdminService(session).set_branch_active(
                    branch.id, False
                )
            assert branch.is_active is True
            assert source.is_active is True
            assert shipment.status == "exception"
    finally:
        await session.rollback()
        await session.close()
        await outer_transaction.rollback()
        await connection.close()
        await database.close()



@pytest.mark.asyncio
async def test_admin_html_role_change_preserves_account_and_blocks_self_demotion():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database
    from medarchive.dependencies import get_pg_session

    database = Database(DATABASE_URL)
    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="admin-role-change-integration-secret",
    ))
    actor = User(
        id=uuid.uuid4(),
        username=f"role-admin-{uuid.uuid4().hex[:12]}",
        password_hash="admin-test-hash",
        full_name="Role Change Admin",
        role="admin",
        is_active=True,
    )

    async def admin_override():
        return actor

    async def session_override():
        async with database.session_factory() as session:
            yield session

    app.dependency_overrides[admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override
    branch_id = None
    user_id = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                branch = await PostgreSQLAdminService(session).create_branch(
                    code=f"ROLE-{uuid.uuid4().hex[:10]}",
                    name="Role transition test branch",
                )
                branch_id = branch.id
                employee = await PostgreSQLAdminService(session).create_user(
                    username=f"role-user-{uuid.uuid4().hex[:12]}",
                    full_name="Role Transition Employee",
                    password_hash="preserved-password-hash",
                    role="branch",
                    branch_id=branch.id,
                )
                user_id = employee.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            page = await client.get("/admin/users")
            assert page.status_code == 200
            assert "Role Transition Employee" in page.text

            short_create = await client.post(
                "/admin/users",
                data={
                    "username": f"short-password-{uuid.uuid4().hex[:8]}",
                    "full_name": "Short Password User",
                    "password": "abc",
                    "role": "viewer",
                    "branch_id": "",
                },
            )
            assert short_create.status_code == 422

            short_update = await client.post(
                f"/admin/users/{user_id}/edit",
                data={
                    "full_name": "Role Transition Employee",
                    "role": "branch",
                    "branch_id": str(branch_id),
                    "password": "abc",
                    "is_active": "true",
                },
            )
            assert short_update.status_code == 422

            changed = await client.post(
                f"/admin/users/{user_id}/edit",
                data={
                    "full_name": "Role Transition Employee Updated",
                    "role": "viewer",
                    "branch_id": "",
                    "is_active": "true",
                },
                follow_redirects=False,
            )
            assert changed.status_code == 303
            async with database.session_factory() as session:
                employee = await session.get(User, user_id)
                assert employee is not None
                assert employee.role == "viewer"
                assert employee.branch_id is None
                assert employee.is_active is True
                assert employee.full_name == "Role Transition Employee Updated"
                assert employee.password_hash == "preserved-password-hash"

            # The HTML endpoint must reject removing the current administrator's
            # own administrative role, even when the account remains active.
            self_demotion = await client.post(
                f"/admin/users/{actor.id}/edit",
                data={
                    "full_name": actor.full_name,
                    "role": "viewer",
                    "branch_id": "",
                    "is_active": "true",
                },
            )
            assert self_demotion.status_code == 409
            self_deactivation = await client.post(
                f"/admin/users/{actor.id}/edit",
                data={
                    "full_name": actor.full_name,
                    "role": "admin",
                    "branch_id": "",
                    "is_active": "false",
                },
            )
            assert self_deactivation.status_code == 409
    finally:
        async with database.session_factory() as session:
            if user_id is not None:
                employee = await session.get(User, user_id)
                if employee is not None:
                    await session.delete(employee)
                    await session.flush()
            if branch_id is not None:
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch", Location.branch_id == branch_id
                    )
                )
                if location is not None:
                    await session.delete(location)
                    await session.flush()
                branch = await session.get(Branch, branch_id)
                if branch is not None:
                    await session.delete(branch)
            await session.commit()
        app.dependency_overrides.clear()
        await database.close()


@pytest.mark.asyncio
async def test_admin_branch_edit_updates_logistics_location_on_postgresql():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database
    from medarchive.dependencies import get_pg_session

    database = Database(DATABASE_URL)
    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="admin-branch-edit-integration-secret",
    ))
    actor = User(
        id=uuid.uuid4(),
        username=f"branch-edit-admin-{uuid.uuid4().hex[:10]}",
        password_hash="admin-test-hash",
        full_name="Branch Edit Admin",
        role="admin",
        is_active=True,
    )

    async def admin_override():
        return actor

    async def session_override():
        async with database.session_factory() as session:
            yield session

    app.dependency_overrides[admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override
    branch_id = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                branch = await PostgreSQLAdminService(session).create_branch(
                    code=f"EDIT-{uuid.uuid4().hex[:10]}",
                    name="Initial branch name",
                )
                branch_id = branch.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            page = await client.get("/admin/branches")
            assert page.status_code == 200
            assert "Initial branch name" in page.text

            response = await client.post(
                f"/admin/branches/{branch_id}/edit",
                data={
                    "code": f"UPDATED-{uuid.uuid4().hex[:10]}",
                    "name": "Updated branch name",
                },
                follow_redirects=False,
            )
            assert response.status_code == 303

        async with database.session_factory() as session:
            branch = await session.get(Branch, branch_id)
            location = await session.scalar(
                select(Location).where(
                    Location.kind == "branch",
                    Location.branch_id == branch_id,
                )
            )
            assert branch is not None
            assert branch.code.startswith("UPDATED-")
            assert branch.name == "Updated branch name"
            assert location is not None
            assert location.name == "Updated branch name"
    finally:
        async with database.session_factory() as session:
            if branch_id is not None:
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch", Location.branch_id == branch_id
                    )
                )
                if location is not None:
                    await session.delete(location)
                    await session.flush()
                branch = await session.get(Branch, branch_id)
                if branch is not None:
                    await session.delete(branch)
            await session.commit()
        app.dependency_overrides.clear()
        await database.close()


@pytest.mark.asyncio
async def test_admin_branch_code_uniqueness_is_preserved_on_create_and_edit():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="branch-code-uniqueness-integration-secret",
    ))
    database = await get_pg_database()
    actor = User(
        id=uuid.uuid4(),
        username=f"branch-code-admin-{uuid.uuid4().hex[:12]}",
        password_hash="test-only-hash",
        full_name="Branch Code Admin",
        role="admin",
        is_active=True,
    )

    async def admin_override():
        return actor

    async def session_override():
        async with database.session_factory() as session:
            yield session

    from medarchive.dependencies import get_pg_session
    app.dependency_overrides[admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override

    first_id = second_id = None
    first_code = f"UNIQUE-{uuid.uuid4().hex[:12]}"
    second_code = f"UNIQUE-{uuid.uuid4().hex[:12]}"
    try:
        async with database.session_factory() as session:
            async with session.begin():
                service = PostgreSQLAdminService(session)
                first = await service.create_branch(
                    code=first_code, name="First unique branch"
                )
                second = await service.create_branch(
                    code=second_code, name="Second unique branch"
                )
                first_id, second_id = first.id, second.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            duplicate_create = await client.post(
                "/admin/branches",
                data={"code": first_code, "name": "Duplicate branch"},
            )
            assert duplicate_create.status_code == 409

            duplicate_edit = await client.post(
                f"/admin/branches/{first_id}/edit",
                data={"code": second_code, "name": "Must not overwrite"},
            )
            assert duplicate_edit.status_code == 409

        async with database.session_factory() as session:
            first = await session.get(Branch, first_id)
            second = await session.get(Branch, second_id)
            first_location = await session.scalar(
                select(Location).where(
                    Location.kind == "branch", Location.branch_id == first_id
                )
            )
            second_location = await session.scalar(
                select(Location).where(
                    Location.kind == "branch", Location.branch_id == second_id
                )
            )
            assert first is not None
            assert second is not None
            assert first.code == first_code
            assert first.name == "First unique branch"
            assert second.code == second_code
            assert second.name == "Second unique branch"
            assert first_location is not None
            assert first_location.name == "First unique branch"
            assert second_location is not None
            assert second_location.name == "Second unique branch"
            assert await session.scalar(
                select(Branch.id).where(Branch.name == "Duplicate branch")
            ) is None
    finally:
        async with database.session_factory() as session:
            for branch_id in (first_id, second_id):
                if branch_id is None:
                    continue
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch", Location.branch_id == branch_id
                    )
                )
                if location is not None:
                    await session.delete(location)
                    await session.flush()
                branch = await session.get(Branch, branch_id)
                if branch is not None:
                    await session.delete(branch)
            await session.commit()
        app.dependency_overrides.clear()
        await database.close()


@pytest.mark.asyncio
async def test_inactive_branch_reactivation_restores_location_and_user_assignment():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database

    database = Database(DATABASE_URL)
    branch_id = user_id = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                service = PostgreSQLAdminService(session)
                branch = await service.create_branch(
                    code=f"REACT-{uuid.uuid4().hex[:12]}",
                    name="Reactivation integration branch",
                )
                branch_id = branch.id
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch",
                        Location.branch_id == branch.id,
                    )
                )
                assert location is not None and location.is_active is True

                await service.set_branch_active(branch.id, False)
                await session.refresh(branch)
                await session.refresh(location)
                assert branch.is_active is False
                assert location.is_active is False

                with pytest.raises(ValueError, match="действующий филиал"):
                    await service.create_user(
                        username=f"inactive-branch-{uuid.uuid4().hex[:12]}",
                        full_name="Inactive Branch Employee",
                        password_hash="test-hash",
                        role="branch",
                        branch_id=branch.id,
                    )

                await service.set_branch_active(branch.id, True)
                await session.refresh(branch)
                await session.refresh(location)
                assert branch.is_active is True
                assert location.is_active is True

                employee = await service.create_user(
                    username=f"reactivated-branch-{uuid.uuid4().hex[:12]}",
                    full_name="Reactivated Branch Employee",
                    password_hash="test-hash",
                    role="branch",
                    branch_id=branch.id,
                )
                user_id = employee.id
                assert employee.is_active is True
                assert employee.branch_id == branch.id
                assert employee.role == "branch"
    finally:
        async with database.session_factory() as session:
            if user_id is not None:
                employee = await session.get(User, user_id)
                if employee is not None:
                    await session.delete(employee)
                    await session.flush()
            if branch_id is not None:
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch",
                        Location.branch_id == branch_id,
                    )
                )
                if location is not None:
                    await session.delete(location)
                    await session.flush()
                branch = await session.get(Branch, branch_id)
                if branch is not None:
                    await session.delete(branch)
            await session.commit()
        await database.close()

@pytest.mark.asyncio
async def test_admin_user_api_create_update_and_self_protection_on_postgresql():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.dependencies import get_pg_session
    from medarchive.routers.postgresql_admin import admin_only as api_admin_only

    database = await get_pg_database()
    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="admin-user-api-integration-secret",
    ))
    actor = User(
        id=uuid.uuid4(),
        username=f"user-api-admin-{uuid.uuid4().hex[:10]}",
        password_hash="admin-test-hash",
        full_name="User API Admin",
        role="admin",
        is_active=True,
    )

    async def admin_override():
        return actor

    async def session_override():
        async with database.session_factory() as session:
            yield session

    app.dependency_overrides[api_admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override
    branch_id = user_id = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                branch = await PostgreSQLAdminService(session).create_branch(
                    code=f"USER-API-{uuid.uuid4().hex[:10]}",
                    name="User API integration branch",
                )
                branch_id = branch.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            too_short = await client.post(
                "/api/admin/users",
                json={
                    "username": f"short-{uuid.uuid4().hex[:8]}",
                    "full_name": "Short Password",
                    "password": "abc",
                    "role": "viewer",
                    "branch_id": None,
                },
            )
            assert too_short.status_code == 422

            created = await client.post(
                "/api/admin/users",
                json={
                    "username": f"user-api-{uuid.uuid4().hex[:10]}",
                    "full_name": "API Branch Employee",
                    "password": "abcd",
                    "role": "branch",
                    "branch_id": str(branch_id),
                },
            )
            assert created.status_code == 201, created.text
            payload = created.json()
            user_id = uuid.UUID(payload["id"])
            assert payload["role"] == "branch"
            assert payload["branch_id"] == str(branch_id)
            assert payload["is_active"] is True
            assert "password_hash" not in payload

            async with database.session_factory() as session:
                employee = await session.get(User, user_id)
                assert employee is not None
                original_hash = employee.password_hash
                assert original_hash != "abcd"

            changed = await client.put(
                f"/api/admin/users/{user_id}",
                json={
                    "full_name": "API Employee Updated",
                    "role": "viewer",
                    "branch_id": None,
                },
            )
            assert changed.status_code == 200, changed.text
            assert changed.json()["role"] == "viewer"
            assert changed.json()["branch_id"] is None
            assert changed.json()["is_active"] is True

            async with database.session_factory() as session:
                employee = await session.get(User, user_id)
                assert employee is not None
                assert employee.full_name == "API Employee Updated"
                assert employee.role == "viewer"
                assert employee.branch_id is None
                assert employee.is_active is True
                assert employee.password_hash == original_hash

            self_demotion = await client.put(
                f"/api/admin/users/{actor.id}",
                json={
                    "full_name": actor.full_name,
                    "role": "viewer",
                    "branch_id": None,
                },
            )
            assert self_demotion.status_code == 409

            self_deactivation = await client.put(
                f"/api/admin/users/{actor.id}",
                json={
                    "full_name": actor.full_name,
                    "role": "admin",
                    "branch_id": None,
                    "is_active": False,
                },
            )
            assert self_deactivation.status_code == 409
    finally:
        async with database.session_factory() as session:
            if user_id is not None:
                employee = await session.get(User, user_id)
                if employee is not None:
                    await session.delete(employee)
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
        app.dependency_overrides.clear()
        await database.close()

@pytest.mark.asyncio
async def test_admin_user_api_activation_and_last_admin_guard_on_postgresql():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.dependencies import get_pg_session
    from medarchive.routers.postgresql_admin import admin_only as api_admin_only

    database = await get_pg_database()
    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="admin-user-state-api-integration-secret",
    ))
    actor = User(
        id=uuid.uuid4(),
        username=f"user-state-api-actor-{uuid.uuid4().hex[:8]}",
        password_hash="admin-test-hash",
        full_name="User State API Admin",
        role="admin",
        is_active=True,
    )
    target_admin = User(
        id=uuid.uuid4(),
        username=f"target-admin-{uuid.uuid4().hex[:8]}",
        password_hash="test-hash",
        full_name="Target Admin",
        role="admin",
        is_active=True,
    )
    standby_admin = User(
        id=uuid.uuid4(),
        username=f"standby-admin-{uuid.uuid4().hex[:8]}",
        password_hash="test-hash",
        full_name="Standby Admin",
        role="admin",
        is_active=False,
    )
    employee = User(
        id=uuid.uuid4(),
        username=f"state-employee-{uuid.uuid4().hex[:8]}",
        password_hash="test-hash",
        full_name="State Employee",
        role="viewer",
        is_active=True,
    )

    async def admin_override():
        return actor

    async def session_override():
        async with database.session_factory() as session:
            yield session

    app.dependency_overrides[api_admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override
    try:
        async with database.session_factory() as session:
            session.add_all([target_admin, standby_admin, employee])
            await session.commit()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            last_admin = await client.put(
                f"/api/admin/users/{target_admin.id}",
                json={
                    "full_name": target_admin.full_name,
                    "role": "viewer",
                    "branch_id": None,
                },
            )
            assert last_admin.status_code == 409
            assert "последнего активного администратора" in last_admin.json()["detail"]

            activate_admin = await client.put(
                f"/api/admin/users/{standby_admin.id}",
                json={
                    "full_name": standby_admin.full_name,
                    "role": "admin",
                    "branch_id": None,
                    "is_active": True,
                },
            )
            assert activate_admin.status_code == 200, activate_admin.text
            assert activate_admin.json()["is_active"] is True

            demote = await client.put(
                f"/api/admin/users/{target_admin.id}",
                json={
                    "full_name": target_admin.full_name,
                    "role": "viewer",
                    "branch_id": None,
                },
            )
            assert demote.status_code == 200, demote.text
            assert demote.json()["role"] == "viewer"

            deactivate = await client.put(
                f"/api/admin/users/{employee.id}",
                json={
                    "full_name": employee.full_name,
                    "role": "viewer",
                    "branch_id": None,
                    "is_active": False,
                },
            )
            assert deactivate.status_code == 200, deactivate.text
            assert deactivate.json()["is_active"] is False

            reactivate = await client.put(
                f"/api/admin/users/{employee.id}",
                json={
                    "full_name": employee.full_name,
                    "role": "viewer",
                    "branch_id": None,
                    "is_active": True,
                },
            )
            assert reactivate.status_code == 200, reactivate.text
            assert reactivate.json()["is_active"] is True

        async with database.session_factory() as session:
            persisted = {
                item.id: item
                for item in (
                    await session.execute(
                        select(User).where(User.id.in_([
                            target_admin.id, standby_admin.id, employee.id
                        ]))
                    )
                ).scalars().all()
            }
            assert persisted[target_admin.id].role == "viewer"
            assert persisted[standby_admin.id].role == "admin"
            assert persisted[standby_admin.id].is_active is True
            assert persisted[employee.id].is_active is True
    finally:
        async with database.session_factory() as session:
            for user_id in (target_admin.id, standby_admin.id, employee.id):
                user = await session.get(User, user_id)
                if user is not None:
                    await session.delete(user)
            await session.commit()
        app.dependency_overrides.clear()
        await database.close()



@pytest.mark.asyncio
async def test_admin_html_user_creation_validates_branch_and_normalizes_login():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database
    from medarchive.dependencies import get_pg_session

    database = Database(DATABASE_URL)
    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="admin-html-user-create-integration-secret",
    ))
    actor = User(
        id=uuid.uuid4(),
        username=f"html-create-admin-{uuid.uuid4().hex[:10]}",
        password_hash="admin-test-hash",
        full_name="HTML Create Admin",
        role="admin",
        is_active=True,
    )

    async def admin_override():
        return actor

    async def session_override():
        async with database.session_factory() as session:
            yield session

    app.dependency_overrides[admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override
    branch_id = user_id = None
    username = f"html-user-{uuid.uuid4().hex[:10]}"
    try:
        async with database.session_factory() as session:
            async with session.begin():
                branch = await PostgreSQLAdminService(session).create_branch(
                    code=f"HTML-USER-{uuid.uuid4().hex[:10]}",
                    name="HTML user creation branch",
                )
                branch_id = branch.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing_branch = await client.post(
                "/admin/users",
                data={
                    "username": f"missing-{uuid.uuid4().hex[:8]}",
                    "full_name": "Branch Without Location",
                    "password": "abcd",
                    "role": "branch",
                    "branch_id": "",
                },
            )
            assert missing_branch.status_code == 422

            created = await client.post(
                "/admin/users",
                data={
                    "username": username.upper(),
                    "full_name": "HTML Created Employee",
                    "password": "abcd",
                    "role": "branch",
                    "branch_id": str(branch_id),
                },
                follow_redirects=False,
            )
            assert created.status_code == 303

            async with database.session_factory() as session:
                employee = await session.scalar(
                    select(User).where(User.username == username.lower())
                )
                assert employee is not None
                user_id = employee.id
                assert employee.full_name == "HTML Created Employee"
                assert employee.role == "branch"
                assert employee.branch_id == branch_id
                assert employee.is_active is True
                assert employee.password_hash != "abcd"

            duplicate = await client.post(
                "/admin/users",
                data={
                    "username": username,
                    "full_name": "Duplicate Employee",
                    "password": "abcd",
                    "role": "viewer",
                    "branch_id": "",
                },
            )
            assert duplicate.status_code == 422

            page = await client.get("/admin/users")
            assert page.status_code == 200
            assert "HTML Created Employee" in page.text

        async with database.session_factory() as session:
            employee = await session.get(User, user_id)
            assert employee is not None
            assert employee.username == username.lower()
            assert employee.full_name == "HTML Created Employee"
            assert employee.role == "branch"
            assert employee.branch_id == branch_id
    finally:
        async with database.session_factory() as session:
            if user_id is not None:
                employee = await session.get(User, user_id)
                if employee is not None:
                    await session.delete(employee)
                    await session.flush()
            if branch_id is not None:
                location = await session.scalar(
                    select(Location).where(
                        Location.kind == "branch", Location.branch_id == branch_id
                    )
                )
                if location is not None:
                    await session.delete(location)
                    await session.flush()
                branch = await session.get(Branch, branch_id)
                if branch is not None:
                    await session.delete(branch)
            await session.commit()
        app.dependency_overrides.clear()
        await database.close()

@pytest.mark.asyncio
async def test_admin_user_api_rejects_assignment_to_inactive_branch_then_allows_reactivation():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.dependencies import get_pg_session
    from medarchive.routers.postgresql_admin import admin_only as api_admin_only

    database = await get_pg_database()
    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="inactive-branch-user-api-secret",
    ))
    actor = User(
        id=uuid.uuid4(),
        username=f"inactive-branch-api-admin-{uuid.uuid4().hex[:8]}",
        password_hash="admin-test-hash",
        full_name="Inactive Branch API Admin",
        role="admin",
        is_active=True,
    )

    async def admin_override():
        return actor

    async def session_override():
        async with database.session_factory() as session:
            yield session

    app.dependency_overrides[api_admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override
    branch_id = user_id = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                service = PostgreSQLAdminService(session)
                branch = await service.create_branch(
                    code=f"INACTIVE-API-{uuid.uuid4().hex[:8]}",
                    name="Inactive assignment branch",
                )
                branch_id = branch.id
                await service.set_branch_active(branch.id, False)
                employee = await service.create_user(
                    username=f"inactive-api-employee-{uuid.uuid4().hex[:8]}",
                    full_name="Unassigned API Employee",
                    password_hash="test-only-hash",
                    role="viewer",
                    branch_id=None,
                )
                user_id = employee.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            rejected = await client.put(
                f"/api/admin/users/{user_id}",
                json={
                    "full_name": "Unassigned API Employee",
                    "role": "branch",
                    "branch_id": str(branch_id),
                },
            )
            assert rejected.status_code == 409, rejected.text
            assert "действующий филиал" in rejected.json()["detail"]

            async with database.session_factory() as session:
                unchanged = await session.get(User, user_id)
                assert unchanged is not None
                assert unchanged.role == "viewer"
                assert unchanged.branch_id is None
                inactive_branch = await session.get(Branch, branch_id)
                assert inactive_branch is not None
                assert inactive_branch.is_active is False

            async with database.session_factory() as session:
                async with session.begin():
                    await PostgreSQLAdminService(session).set_branch_active(
                        branch_id, True
                    )

            accepted = await client.put(
                f"/api/admin/users/{user_id}",
                json={
                    "full_name": "Assigned API Employee",
                    "role": "branch",
                    "branch_id": str(branch_id),
                },
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["role"] == "branch"
            assert accepted.json()["branch_id"] == str(branch_id)

            async with database.session_factory() as session:
                assigned = await session.get(User, user_id)
                assert assigned is not None
                assert assigned.full_name == "Assigned API Employee"
                assert assigned.role == "branch"
                assert assigned.branch_id == branch_id
                assert assigned.is_active is True
    finally:
        async with database.session_factory() as session:
            if user_id is not None:
                employee = await session.get(User, user_id)
                if employee is not None:
                    await session.delete(employee)
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
        app.dependency_overrides.clear()
        await database.close()

@pytest.mark.asyncio
async def test_admin_user_api_keeps_role_and_branch_assignment_consistent():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.dependencies import get_pg_session
    from medarchive.routers.postgresql_admin import admin_only as api_admin_only

    database = await get_pg_database()
    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="user-role-branch-consistency-secret",
    ))
    actor = User(
        id=uuid.uuid4(),
        username=f"role-branch-api-admin-{uuid.uuid4().hex[:8]}",
        password_hash="admin-test-hash",
        full_name="Role Branch API Admin",
        role="admin",
        is_active=True,
    )

    async def admin_override():
        return actor

    async def session_override():
        async with database.session_factory() as session:
            yield session

    app.dependency_overrides[api_admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override
    branch_id = user_id = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                branch = await PostgreSQLAdminService(session).create_branch(
                    code=f"ROLE-BRANCH-{uuid.uuid4().hex[:8]}",
                    name="Role assignment branch",
                )
                branch_id = branch.id
                employee = await PostgreSQLAdminService(session).create_user(
                    username=f"role-branch-employee-{uuid.uuid4().hex[:8]}",
                    full_name="Role Branch Employee",
                    password_hash="test-only-hash",
                    role="viewer",
                    branch_id=None,
                )
                user_id = employee.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            invalid_non_branch = await client.put(
                f"/api/admin/users/{user_id}",
                json={
                    "full_name": "Role Branch Employee",
                    "role": "central_office",
                    "branch_id": str(branch_id),
                },
            )
            assert invalid_non_branch.status_code == 409, invalid_non_branch.text

            invalid_branch = await client.put(
                f"/api/admin/users/{user_id}",
                json={
                    "full_name": "Role Branch Employee",
                    "role": "branch",
                    "branch_id": None,
                },
            )
            assert invalid_branch.status_code == 409, invalid_branch.text

            async with database.session_factory() as session:
                unchanged = await session.get(User, user_id)
                assert unchanged is not None
                assert unchanged.role == "viewer"
                assert unchanged.branch_id is None

            assigned = await client.put(
                f"/api/admin/users/{user_id}",
                json={
                    "full_name": "Role Branch Employee",
                    "role": "branch",
                    "branch_id": str(branch_id),
                },
            )
            assert assigned.status_code == 200, assigned.text
            assert assigned.json()["role"] == "branch"
            assert assigned.json()["branch_id"] == str(branch_id)

            moved_to_central = await client.put(
                f"/api/admin/users/{user_id}",
                json={
                    "full_name": "Role Branch Employee",
                    "role": "central_office",
                    "branch_id": None,
                },
            )
            assert moved_to_central.status_code == 200, moved_to_central.text
            assert moved_to_central.json()["role"] == "central_office"
            assert moved_to_central.json()["branch_id"] is None

            async with database.session_factory() as session:
                final_user = await session.get(User, user_id)
                assert final_user is not None
                assert final_user.role == "central_office"
                assert final_user.branch_id is None
    finally:
        async with database.session_factory() as session:
            if user_id is not None:
                employee = await session.get(User, user_id)
                if employee is not None:
                    await session.delete(employee)
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
        app.dependency_overrides.clear()
        await database.close()



@pytest.mark.asyncio
async def test_admin_user_api_rejects_invalid_role_branch_combinations_on_create():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.dependencies import get_pg_session
    from medarchive.routers.postgresql_admin import admin_only as api_admin_only

    database = await get_pg_database()
    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="user-create-role-branch-validation-secret",
    ))
    actor = User(
        id=uuid.uuid4(),
        username=f"create-validation-admin-{uuid.uuid4().hex[:8]}",
        password_hash="admin-test-hash",
        full_name="Create Validation Admin",
        role="admin",
        is_active=True,
    )

    async def admin_override():
        return actor

    async def session_override():
        async with database.session_factory() as session:
            yield session

    app.dependency_overrides[api_admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override
    usernames = [
        f"invalid-role-{uuid.uuid4().hex[:8]}",
        f"missing-branch-{uuid.uuid4().hex[:8]}",
        f"unexpected-branch-{uuid.uuid4().hex[:8]}",
    ]
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payloads = (
                {
                    "username": usernames[0],
                    "full_name": "Invalid Role",
                    "password": "abcd",
                    "role": "superuser",
                    "branch_id": None,
                },
                {
                    "username": usernames[1],
                    "full_name": "Missing Branch",
                    "password": "abcd",
                    "role": "branch",
                    "branch_id": None,
                },
                {
                    "username": usernames[2],
                    "full_name": "Unexpected Branch",
                    "password": "abcd",
                    "role": "central_office",
                    "branch_id": str(uuid.uuid4()),
                },
            )
            for payload in payloads:
                response = await client.post("/api/admin/users", json=payload)
                assert response.status_code == 422, response.text

        async with database.session_factory() as session:
            persisted = await session.scalar(
                select(User.id).where(User.username.in_(usernames))
            )
            assert persisted is None
    finally:
        app.dependency_overrides.clear()
        await database.close()


@pytest.mark.asyncio
async def test_admin_html_user_edit_rejects_invalid_role_branch_combinations():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database
    from medarchive.dependencies import get_pg_session

    database = Database(DATABASE_URL)
    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="html-user-edit-role-branch-validation-secret",
    ))
    actor = User(
        id=uuid.uuid4(),
        username=f"html-edit-admin-{uuid.uuid4().hex[:8]}",
        password_hash="admin-test-hash",
        full_name="HTML Edit Admin",
        role="admin",
        is_active=True,
    )

    async def admin_override():
        return actor

    async def session_override():
        async with database.session_factory() as session:
            yield session

    app.dependency_overrides[admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override
    branch_id = user_id = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                service = PostgreSQLAdminService(session)
                branch = await service.create_branch(
                    code=f"HTML-EDIT-{uuid.uuid4().hex[:8]}",
                    name="HTML edit validation branch",
                )
                branch_id = branch.id
                employee = await service.create_user(
                    username=f"html-edit-user-{uuid.uuid4().hex[:8]}",
                    full_name="HTML Edit Employee",
                    password_hash="original-password-hash",
                    role="branch",
                    branch_id=branch.id,
                )
                user_id = employee.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            invalid_payloads = (
                {"role": "central_office", "branch_id": str(branch_id)},
                {"role": "branch", "branch_id": ""},
                {"role": "unknown_role", "branch_id": ""},
            )
            for invalid in invalid_payloads:
                response = await client.post(
                    f"/admin/users/{user_id}/edit",
                    data={
                        "full_name": "Should Not Persist",
                        "role": invalid["role"],
                        "branch_id": invalid["branch_id"],
                        "is_active": "true",
                    },
                    follow_redirects=False,
                )
                assert response.status_code == 422, response.text

                async with database.session_factory() as session:
                    unchanged = await session.get(User, user_id)
                    assert unchanged is not None
                    assert unchanged.full_name == "HTML Edit Employee"
                    assert unchanged.role == "branch"
                    assert unchanged.branch_id == branch_id
                    assert unchanged.is_active is True
                    assert unchanged.password_hash == "original-password-hash"
    finally:
        async with database.session_factory() as session:
            if user_id is not None:
                employee = await session.get(User, user_id)
                if employee is not None:
                    await session.delete(employee)
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
        app.dependency_overrides.clear()
        await database.close()



@pytest.mark.asyncio
async def test_admin_html_courier_deactivation_is_blocked_by_draft_shipment():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from medarchive.adapters.postgresql.database import Database
    from medarchive.dependencies import get_pg_session
    from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow

    database = Database(DATABASE_URL)
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="html-courier-deactivation-guard-secret",
    ))
    admin = User(
        id=uuid.uuid4(),
        username=f"courier-html-admin-{uuid.uuid4().hex[:8]}",
        password_hash="admin-test-hash",
        full_name="Courier HTML Admin",
        role="admin",
        is_active=True,
    )

    async def admin_override():
        return admin

    async def session_override():
        yield session

    app.dependency_overrides[admin_only] = admin_override
    app.dependency_overrides[get_pg_session] = session_override

    try:
        async with session.begin():
            branch = Branch(
                code=f"COURIER-HTML-{uuid.uuid4().hex[:8]}",
                name="Courier HTML guard branch",
                is_active=True,
            )
            session.add(branch)
            await session.flush()
            source = Location(
                kind="branch", branch_id=branch.id,
                name=branch.name, is_active=True,
            )
            actor = User(
                username=f"courier-html-actor-{uuid.uuid4().hex[:8]}",
                password_hash="test-hash",
                full_name="Courier HTML Branch Actor",
                role="branch",
                branch_id=branch.id,
                is_active=True,
            )
            courier = Courier(
                full_name=f"Courier HTML {uuid.uuid4().hex[:8]}",
                is_active=True,
            )
            session.add_all([source, actor, courier])
            await session.flush()
            target = await session.scalar(
                select(Location).where(
                    Location.kind == "central_office",
                    Location.is_active.is_(True),
                )
            )
            if target is None:
                target = Location(
                    kind="central_office", branch_id=None,
                    name="Test Central Office", is_active=True,
                )
                session.add(target)
                await session.flush()

            shipment = await PostgreSQLShipmentWorkflow(session).create_draft(
                from_location_id=source.id,
                to_location_id=target.id,
                courier_id=courier.id,
                issued_by=actor.id,
            )
            courier_id = courier.id
            shipment_id = shipment.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/admin/couriers/{courier_id}/status",
                data={"is_active": "false"},
                follow_redirects=False,
            )
            assert response.status_code == 409, response.text

        persisted_courier = await session.get(Courier, courier_id)
        persisted_shipment = await session.get(Shipment, shipment_id)
        assert persisted_courier is not None and persisted_courier.is_active is True
        assert persisted_shipment is not None and persisted_shipment.status == "draft"
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await outer_transaction.rollback()
        await connection.close()
        await database.close()

@pytest.mark.asyncio
async def test_admin_incident_list_and_detail_render_exception_shipment_on_postgresql():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    from datetime import datetime, timezone

    from medarchive.adapters.postgresql.models import Shipment

    app = create_app(Config(
        database_url=DATABASE_URL,
        postgres_enabled=True,
        auth_backend="postgresql",
        secret_key="integration-test-secret",
    ))
    database = await get_pg_database()
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    actor_id = uuid.uuid4()
    try:
        async with session.begin():
                branch = Branch(
                    code=f"INC-{uuid.uuid4().hex[:10]}",
                    name="Incident admin test branch",
                    is_active=True,
                )
                session.add(branch)
                await session.flush()
                branch_id = branch.id
                source = Location(
                    kind="branch", branch_id=branch.id,
                    name=branch.name, is_active=True,
                )
                target = Location(
                    kind="central_office", branch_id=None,
                    name=f"Incident target {uuid.uuid4().hex[:6]}",
                    is_active=True,
                )
                actor = User(
                    id=actor_id,
                    username=f"incident-admin-{uuid.uuid4().hex[:10]}",
                    password_hash="test-only-hash",
                    full_name="Incident Admin",
                    role="admin",
                    is_active=True,
                )
                reporter = User(
                    username=f"incident-reporter-{uuid.uuid4().hex[:10]}",
                    password_hash="test-only-hash",
                    full_name="Incident Branch Reporter",
                    role="branch",
                    branch_id=branch.id,
                    is_active=True,
                )
                receiver = User(
                    username=f"incident-receiver-{uuid.uuid4().hex[:10]}",
                    password_hash="test-only-hash",
                    full_name="Incident Central Receiver",
                    role="central_office",
                    branch_id=None,
                    is_active=True,
                )
                courier = Courier(
                    full_name=f"Incident Courier {uuid.uuid4().hex[:8]}",
                    phone=None,
                    is_active=True,
                )
                session.add_all([source, target, actor, reporter, receiver, courier])
                await session.flush()
                location_ids = [source.id, target.id]
                reporter_id = reporter.id
                courier_id = courier.id

                # Build the incident through the real workflow: a non-empty
                # manifest is handed to the courier before the reporter flags it.
                card = Card(
                    card_number=f"INC-CARD-{uuid.uuid4().hex[:10]}",
                    full_name="Incident Test Patient",
                    date_of_birth=date(1980, 1, 1),
                    origin_branch_id=branch.id,
                    current_location_id=source.id,
                    current_shipment_id=None,
                    created_by=reporter.id,
                )
                session.add(card)
                await session.flush()
                from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow
                workflow = PostgreSQLShipmentWorkflow(session)
                shipment = await workflow.create_draft(
                    from_location_id=source.id,
                    to_location_id=target.id,
                    courier_id=courier.id,
                    issued_by=reporter.id,
                )
                await workflow.add_standalone_card(
                    shipment_id=shipment.id,
                    card_id=card.id,
                    actor_id=reporter.id,
                )
                await workflow.hand_over(
                    shipment_id=shipment.id,
                    actor_id=reporter.id,
                )
                await workflow.register_exception(
                    shipment_id=shipment.id,
                    actor_id=reporter.id,
                    note="Тестовый инцидент: повреждена упаковка",
                )
                shipment_id = shipment.id

                # A resolved incident remains part of the administrative audit
                # history after the shipment reaches its received state.
                resolved_card = Card(
                    card_number=f"INC-RESOLVED-{uuid.uuid4().hex[:10]}",
                    full_name="Resolved Incident Patient",
                    date_of_birth=date(1982, 2, 2),
                    origin_branch_id=branch.id,
                    current_location_id=source.id,
                    current_shipment_id=None,
                    created_by=reporter.id,
                )
                session.add(resolved_card)
                await session.flush()
                resolved_shipment = await workflow.create_draft(
                    from_location_id=source.id,
                    to_location_id=target.id,
                    courier_id=courier.id,
                    issued_by=reporter.id,
                )
                await workflow.add_standalone_card(
                    shipment_id=resolved_shipment.id,
                    card_id=resolved_card.id,
                    actor_id=reporter.id,
                )
                await workflow.hand_over(
                    shipment_id=resolved_shipment.id,
                    actor_id=reporter.id,
                )
                await workflow.register_exception(
                    shipment_id=resolved_shipment.id,
                    actor_id=reporter.id,
                    note="Тестовый разрешённый инцидент",
                )
                await workflow.resolve_exception(
                    shipment_id=resolved_shipment.id,
                    actor_id=receiver.id,
                    resolution_note="Полный манифест проверен, отправление принято",
                )
                resolved_shipment_id = resolved_shipment.id

        async def admin_override():
            return actor

        async def session_override():
            yield session

        app.dependency_overrides[admin_only] = admin_override
        from medarchive.dependencies import get_pg_session
        app.dependency_overrides[get_pg_session] = session_override

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listing = await client.get("/admin/incidents")
            assert listing.status_code == 200
            assert "Тестовый инцидент: повреждена упаковка" in listing.text
            assert "Incident Courier" in listing.text
            assert f"/admin/incidents/{shipment_id}" in listing.text
            assert "Тестовый разрешённый инцидент" in listing.text
            assert f"/admin/incidents/{resolved_shipment_id}" in listing.text
            assert "received" in listing.text

            resolved_detail = await client.get(
                f"/admin/incidents/{resolved_shipment_id}"
            )
            assert resolved_detail.status_code == 200
            assert "Тестовый разрешённый инцидент" in resolved_detail.text
            assert "Полный манифест проверен, отправление принято" in resolved_detail.text
            assert "Incident Central Receiver" in resolved_detail.text
            assert "received" in resolved_detail.text
            assert "Resolved Incident Patient" in resolved_detail.text

            detail = await client.get(f"/admin/incidents/{shipment_id}")
            assert detail.status_code == 200
            assert "Incident Branch Reporter" in detail.text
            assert "Тестовый инцидент: повреждена упаковка" in detail.text
            assert "Коробок в отправлении нет." in detail.text
            assert "Incident Test Patient" in detail.text

            missing = await client.get(f"/admin/incidents/{uuid.uuid4()}")
            assert missing.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await outer_transaction.rollback()
        await connection.close()
        await database.close()
