"""PostgreSQL transfer page route coverage."""

import os
import uuid

import httpx
import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import Branch, Courier, Location, Shipment, User
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app

DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_postgresql_branch_transfer_page_is_scoped_to_branch():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(Config(database_url=DATABASE_URL, postgres_enabled=True,
                            auth_backend="postgresql", secret_key="postgres-transfer-integration-secret"))
    database = await get_pg_database()
    branch_id = uuid.uuid4()
    actor = User(id=uuid.uuid4(), username=f"transfer-{uuid.uuid4().hex[:12]}",
                 password_hash="test-only-hash", full_name="Transfer Integration",
                 role="branch", branch_id=branch_id, is_active=True)
    branch = Branch(id=branch_id, code=f"TR-{uuid.uuid4().hex[:16]}",
                    name="Transfer Integration Branch", is_active=True)
    location = Location(id=uuid.uuid4(), kind="branch", branch_id=branch_id,
                        name=branch.name, is_active=True)
    archive_actor = User(id=uuid.uuid4(), username=f"archive-{uuid.uuid4().hex[:12]}",
                         password_hash="test-only-hash", full_name="Archive Operator",
                         role="archive", is_active=True)
    central_actor = User(id=uuid.uuid4(), username=f"central-{uuid.uuid4().hex[:12]}",
                         password_hash="test-only-hash", full_name="Central Operator",
                         role="central_office", is_active=True)
    viewer = User(id=uuid.uuid4(), username=f"viewer-{uuid.uuid4().hex[:12]}",
                  password_hash="test-only-hash", full_name="Read Only Viewer",
                  role="viewer", is_active=True)
    try:
        async with database.session_factory() as session:
            async with session.begin():
                session.add_all([branch, location, actor, archive_actor, central_actor, viewer])
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            token = get_auth().create_session_token(actor)
            response = await client.get("/transfer", cookies={"session": token})
            receive = await client.get("/branch/receive", cookies={"session": token})
            assert response.status_code == 200
            assert "Передача курьеру" in response.text
            assert 'class="card operation-panel transfer-panel"' in response.text
            assert "Нет коробок, готовых к отправке" in response.text
            shipments_page = await client.get("/shipments", cookies={"session": token})
            assert shipments_page.status_code == 200
            assert "Отправления" in shipments_page.text
            assert receive.status_code == 200
            assert "Приёмка отправлений" in receive.text
            for page in (response, receive):
                assert 'href="/search"' in page.text
                assert 'href="/history"' in page.text
            for user in (archive_actor, central_actor, viewer):
                forbidden = await client.get("/transfer", cookies={
                    "session": get_auth().create_session_token(user)})
                assert forbidden.status_code == 403

            # Keep the complete role boundary matrix for operational routes.
            for path in ("/receive", "/placement", "/requests", "/central/receive"):
                denied = await client.get(path, cookies={"session": token})
                assert denied.status_code == 403, (path, denied.status_code)

            archive_token = get_auth().create_session_token(archive_actor)
            for path in ("/transfer", "/branch/receive", "/central/receive"):
                denied = await client.get(path, cookies={"session": archive_token})
                assert denied.status_code == 403, (path, denied.status_code)

            central_token = get_auth().create_session_token(central_actor)
            for path in ("/transfer", "/branch/receive", "/receive", "/placement", "/requests"):
                denied = await client.get(path, cookies={"session": central_token})
                assert denied.status_code == 403, (path, denied.status_code)

            viewer_token = get_auth().create_session_token(viewer)
            for path in ("/transfer", "/branch/receive", "/receive", "/placement", "/requests", "/central/receive"):
                denied = await client.get(path, cookies={"session": viewer_token})
                assert denied.status_code == 403, (path, denied.status_code)
    finally:
        async with database.session_factory() as session:
            await session.execute(Location.__table__.delete().where(Location.branch_id == branch_id))
            for user in (actor, archive_actor, central_actor, viewer):
                persisted = await session.get(User, user.id)
                if persisted is not None:
                    await session.delete(persisted)
            await session.flush()
            persisted_branch = await session.get(Branch, branch_id)
            if persisted_branch is not None:
                await session.delete(persisted_branch)
            await session.commit()
        await database.close()


@pytest.mark.asyncio
async def test_postgresql_shipment_register_and_detail_are_location_scoped():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(Config(database_url=DATABASE_URL, postgres_enabled=True,
                            auth_backend="postgresql", secret_key="postgres-shipment-register-secret"))
    database = await get_pg_database()
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint",
    )

    async def session_override():
        yield session

    app.dependency_overrides[get_pg_session] = session_override

    branch_id = uuid.uuid4()
    branch = Branch(id=branch_id, code="SR-" + uuid.uuid4().hex[:16],
                    name="Shipment Register Branch", is_active=True)
    branch_location = Location(id=uuid.uuid4(), kind="branch", branch_id=branch_id,
                               name=branch.name, is_active=True)
    branch_user = User(id=uuid.uuid4(), username="shipbranch-" + uuid.uuid4().hex[:12],
                       password_hash="test-only-hash", full_name="Branch Shipment User",
                       role="branch", branch_id=branch_id, is_active=True)
    archive_user = User(id=uuid.uuid4(), username="shiparchive-" + uuid.uuid4().hex[:12],
                        password_hash="test-only-hash", full_name="Archive Shipment User",
                        role="archive", is_active=True)
    central_user = User(id=uuid.uuid4(), username="shipcentral-" + uuid.uuid4().hex[:12],
                        password_hash="test-only-hash", full_name="Central Shipment User",
                        role="central_office", is_active=True)
    viewer = User(id=uuid.uuid4(), username="shipviewer-" + uuid.uuid4().hex[:12],
                  password_hash="test-only-hash", full_name="Shipment Viewer",
                  role="viewer", is_active=True)
    admin_user = User(id=uuid.uuid4(), username="shipadmin-" + uuid.uuid4().hex[:12],
                      password_hash="test-only-hash", full_name="Shipment Admin",
                      role="admin", is_active=True)
    courier = Courier(id=uuid.uuid4(), full_name="Register Test Courier", is_active=True)

    try:
        async with session.begin():
            archive_location = await session.scalar(
                select(Location).where(Location.kind == "archive", Location.is_active.is_(True))
            )
            central_location = await session.scalar(
                select(Location).where(Location.kind == "central_office", Location.is_active.is_(True))
            )
            if archive_location is None:
                archive_location = Location(
                    id=uuid.uuid4(), kind="archive", name="Shipment Register Archive", is_active=True,
                )
                session.add(archive_location)
            if central_location is None:
                central_location = Location(
                    id=uuid.uuid4(), kind="central_office", name="Shipment Register Central", is_active=True,
                )
                session.add(central_location)

            session.add(branch)
            await session.flush()
            session.add_all([
                branch_location, branch_user, archive_user, central_user, viewer,
                admin_user, courier,
            ])
            await session.flush()

            shipment = Shipment(
                id=uuid.uuid4(), from_location_id=branch_location.id,
                to_location_id=archive_location.id, courier_id=courier.id,
                issued_by=branch_user.id, status="draft",
            )
            central_shipment = Shipment(
                id=uuid.uuid4(), from_location_id=central_location.id,
                to_location_id=archive_location.id, courier_id=courier.id,
                issued_by=central_user.id, status="draft",
            )
            session.add_all([shipment, central_shipment])
            await session.flush()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            async def get_as(user, page):
                return await client.get(page, cookies={
                    "session": get_auth().create_session_token(user)
                })

            for user in (branch_user, archive_user):
                listing = await get_as(user, "/shipments")
                detail = await get_as(user, "/shipments/" + str(shipment.id))
                assert listing.status_code == 200
                assert "№ " + str(shipment.shipment_number) in listing.text
                assert detail.status_code == 200
                assert "Shipment Register Branch" in detail.text
                assert "Основные сведения" in detail.text

            central_list = await get_as(central_user, "/shipments")
            central_detail = await get_as(central_user, "/shipments/" + str(central_shipment.id))
            hidden_detail = await get_as(central_user, "/shipments/" + str(shipment.id))
            assert central_list.status_code == 200
            assert "№ " + str(central_shipment.shipment_number) in central_list.text
            assert "№ " + str(shipment.shipment_number) not in central_list.text
            assert central_detail.status_code == 200
            assert hidden_detail.status_code == 404
            assert (await get_as(viewer, "/shipments")).status_code == 403
            assert (await get_as(admin_user, "/shipments")).status_code == 403

            # Exercise every PostgreSQL HTML page through the real router
            # registration and session authentication, not dependency mocks.
            shared_pages = (
                "/dashboard", "/search", "/search/results?query=scope", "/history",
            )
            for role_user in (branch_user, archive_user, central_user, viewer, admin_user):
                for page_path in shared_pages:
                    page = await get_as(role_user, page_path)
                    assert page.status_code == 200, (
                        role_user.role, page_path, page.status_code, page.text[:500]
                    )

            role_pages = {
                "branch": ("/packing", "/transfer", "/branch/receive"),
                "archive": ("/receive", "/placement", "/requests"),
                "central_office": (
                    "/requests/new", "/central/receive", "/central/packing",
                    "/central/transfer", "/central/custody",
                ),
                "admin": (
                    "/admin", "/admin/branches", "/admin/users", "/admin/couriers",
                    "/admin/sections", "/admin/incidents", "/admin/audit",
                ),
            }
            all_role_pages = tuple(path for pages in role_pages.values() for path in pages)
            users_by_role = {
                "branch": branch_user, "archive": archive_user,
                "central_office": central_user, "admin": admin_user,
            }
            for role, pages in role_pages.items():
                token_user = users_by_role[role]
                for page_path in pages:
                    page = await get_as(token_user, page_path)
                    assert page.status_code == 200, (
                        role, page_path, page.status_code, page.text[:500]
                    )
                for other_role, other_user in (
                    ("branch", branch_user), ("archive", archive_user),
                    ("central_office", central_user), ("viewer", viewer),
                    ("admin", admin_user),
                ):
                    if other_role == role:
                        continue
                    for page_path in pages:
                        denied = await get_as(other_user, page_path)
                        assert denied.status_code == 403, (
                            other_role, page_path, denied.status_code
                        )

            # Sidebar links must match each role's permitted workflow.
            expected_navigation = {
                "branch": (
                    "/dashboard", "/search", "/history", "/shipments",
                    "/branch/receive", "/packing", "/transfer",
                ),
                "archive": (
                    "/dashboard", "/search", "/history", "/shipments",
                    "/receive", "/placement", "/requests",
                ),
                "central_office": (
                    "/dashboard", "/search", "/history", "/shipments",
                    "/requests/new", "/central/receive", "/central/packing",
                    "/central/transfer", "/central/custody",
                ),
                "viewer": ("/dashboard", "/search", "/history"),
                "admin": (
                    "/dashboard", "/search", "/history", "/admin",
                ),
            }
            for role, role_user in (
                ("branch", branch_user), ("archive", archive_user),
                ("central_office", central_user), ("viewer", viewer),
                ("admin", admin_user),
            ):
                dashboard = await get_as(role_user, "/dashboard")
                assert dashboard.status_code == 200
                for nav_path in expected_navigation[role]:
                    assert f'href="{nav_path}"' in dashboard.text, (
                        role, "missing navigation", nav_path
                    )
                for forbidden_path in all_role_pages:
                    if forbidden_path not in role_pages.get(role, ()):
                        assert f'href="{forbidden_path}"' not in dashboard.text, (
                            role, "unexpected navigation", forbidden_path
                        )
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
