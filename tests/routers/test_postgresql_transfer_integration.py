"""PostgreSQL transfer page route coverage."""

import os
import uuid

import httpx
import pytest

from sqlalchemy import select

from medarchive.adapters.postgresql.models import Branch, Courier, Location, Shipment, User
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database
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
    branch_id = uuid.uuid4()
    branch = Branch(id=branch_id, code="SR-" + uuid.uuid4().hex[:16], name="Shipment Register Branch", is_active=True)
    branch_location = Location(id=uuid.uuid4(), kind="branch", branch_id=branch_id,
                               name=branch.name, is_active=True)
    archive_location = None
    central_location = None
    created_archive = False
    created_central = False
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
    courier = Courier(id=uuid.uuid4(), full_name="Register Test Courier", is_active=True)
    shipment = Shipment(
        id=uuid.uuid4(), from_location_id=branch_location.id,
        to_location_id=branch_location.id, courier_id=courier.id,
        issued_by=branch_user.id, status="draft",
    )
    central_shipment = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                archive_location = await session.scalar(select(Location).where(Location.kind == "archive"))
                central_location = await session.scalar(select(Location).where(Location.kind == "central_office"))
                if archive_location is None:
                    archive_location = Location(id=uuid.uuid4(), kind="archive", name="Shipment Register Archive", is_active=True)
                    created_archive = True
                if central_location is None:
                    central_location = Location(id=uuid.uuid4(), kind="central_office", name="Shipment Register Central", is_active=True)
                    created_central = True
                shipment.to_location_id = archive_location.id
                central_shipment = Shipment(
                    id=uuid.uuid4(), from_location_id=central_location.id,
                    to_location_id=archive_location.id, courier_id=courier.id,
                    issued_by=central_user.id, status="draft",
                )
                session.add_all([branch, branch_location, branch_user, archive_user, central_user, viewer, courier, shipment, central_shipment])
                if created_archive:
                    session.add(archive_location)
                if created_central:
                    session.add(central_location)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def get_as(user, page):
                return await client.get(page, cookies={"session": get_auth().create_session_token(user)})
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
    finally:
        async with database.session_factory() as session:
            for shipment_id in (shipment.id, central_shipment.id if central_shipment else None):
                if shipment_id is not None:
                    persisted = await session.get(Shipment, shipment_id)
                    if persisted is not None:
                        await session.delete(persisted)
            for user in (branch_user, archive_user, central_user, viewer):
                persisted_user = await session.get(User, user.id)
                if persisted_user is not None:
                    await session.delete(persisted_user)
            persisted_courier = await session.get(Courier, courier.id)
            if persisted_courier is not None:
                await session.delete(persisted_courier)
            await session.flush()
            location_ids = [branch_location.id]
            if created_archive:
                location_ids.append(archive_location.id)
            if created_central:
                location_ids.append(central_location.id)
            for location_id in location_ids:
                persisted_location = await session.get(Location, location_id)
                if persisted_location is not None:
                    await session.delete(persisted_location)
            persisted_branch = await session.get(Branch, branch_id)
            if persisted_branch is not None:
                await session.delete(persisted_branch)
            await session.commit()
        await database.close()
