"""PostgreSQL integration coverage for the administrative incident pages."""
import os
import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from medarchive.adapters.postgresql.models import (
    Branch, Card, Courier, Location, Shipment, ShipmentBox, ShipmentCard, User,
)
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_session
from medarchive.main import create_app
from medarchive.routers.postgresql_admin_pages import admin_only
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_admin_incident_pages_show_manifest_and_enforce_admin_role():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        transaction = await session.begin()
        app = create_app(Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="incident-pages-integration-secret",
        ))

        async def session_override():
            yield session

        app.dependency_overrides[get_pg_session] = session_override

        suffix = uuid.uuid4().hex
        branch_id = uuid.uuid4()
        branch = Branch(
            id=branch_id, code=f"INC-{suffix[:12]}", name="Incident test branch",
            is_active=True,
        )
        branch_location = Location(
            kind="branch", branch_id=branch_id, name="Incident test branch",
            is_active=True,
        )
        archive_location = await session.scalar(
            select(Location).where(Location.kind == "archive", Location.is_active.is_(True))
        )
        assert archive_location is not None

        admin = User(
            username=f"incident-admin-{suffix}", password_hash="test-hash",
            full_name="Incident Admin", role="admin", is_active=True,
        )
        viewer = User(
            username=f"incident-viewer-{suffix}", password_hash="test-hash",
            full_name="Incident Viewer", role="viewer", is_active=True,
        )
        archive_user = User(
            username=f"incident-archive-{suffix}", password_hash="test-hash",
            full_name="Incident Reporter", role="archive", is_active=True,
        )
        courier = Courier(full_name=f"Incident Courier {suffix}", is_active=True)
        session.add_all([branch, branch_location, admin, viewer, archive_user, courier])
        await session.flush()

        card = Card(
            card_number=f"INC-CARD-{suffix}", full_name="Incident Test Patient",
            date_of_birth=date(1980, 1, 2), origin_branch_id=branch.id,
            current_location_id=branch_location.id, status="active",
        )
        session.add(card)
        await session.flush()

        workflow = PostgreSQLShipmentWorkflow(session)
        box = await workflow.create_box(
            location_id=branch_location.id, actor_id=admin.id, card_ids=[card.id],
        )
        shipment = await workflow.create_draft(
            from_location_id=branch_location.id,
            to_location_id=archive_location.id,
            courier_id=courier.id,
            issued_by=admin.id,
        )
        await workflow.add_box(
            shipment_id=shipment.id, box_id=box.id, actor_id=admin.id,
        )
        await workflow.hand_over(shipment_id=shipment.id, actor_id=admin.id)
        await workflow.register_exception(
            shipment_id=shipment.id, actor_id=archive_user.id,
            note="Интеграционная проверка инцидента",
        )
        await session.flush()

        admin_token = get_auth().create_session_token(admin)
        viewer_token = get_auth().create_session_token(viewer)
        shipment_id = shipment.id
        box_number = box.box_number

        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                client.cookies.set("session", admin_token)
                listing = await client.get("/admin/incidents")
                assert listing.status_code == 200
                assert str(shipment.shipment_number) in listing.text
                assert "Интеграционная проверка инцидента" in listing.text
                assert "Incident test branch" in listing.text

                detail = await client.get(f"/admin/incidents/{shipment_id}")
                assert detail.status_code == 200
                assert "Incident Reporter" in detail.text
                assert "Incident Test Patient" in detail.text
                assert f"INC-CARD-{suffix}" in detail.text
                assert box_number in detail.text

                missing = await client.get(f"/admin/incidents/{uuid.uuid4()}")
                assert missing.status_code == 404

                client.cookies.set("session", viewer_token)
                forbidden = await client.get("/admin/incidents")
                assert forbidden.status_code == 403
                forbidden_detail = await client.get(f"/admin/incidents/{shipment_id}")
                assert forbidden_detail.status_code == 403
        finally:
            app.dependency_overrides.clear()
            await transaction.rollback()
    await engine.dispose()
