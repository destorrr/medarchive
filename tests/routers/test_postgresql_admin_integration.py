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
            if courier_id is not None:
                courier = await session.get(Courier, courier_id)
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
