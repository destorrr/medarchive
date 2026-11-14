"""PostgreSQL integration coverage for the administrative incident pages."""
import os
import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from medarchive.adapters.postgresql.models import (
    Branch, Card, Courier, DomainEvent, Location, ShipmentCard, User,
)
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_session
from medarchive.main import create_app
from medarchive.services.postgresql_bootstrap import ensure_system_locations
from medarchive.services.postgresql_shipment_workflow import (
    PostgreSQLShipmentWorkflow, ShipmentWorkflowError,
)


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_admin_incident_pages_show_manifest_and_enforce_admin_role():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    engine = create_async_engine(DATABASE_URL)
    connection = await engine.connect()
    outer_transaction = await connection.begin()
    factory = async_sessionmaker(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint",
    )
    async with factory() as session:
        app = create_app(Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="incident-pages-integration-secret",
        ))

        async def session_override():
            yield session

        app.dependency_overrides[get_pg_session] = session_override

        await ensure_system_locations(session)
        archive_location = await session.scalar(
            select(Location).where(Location.kind == "archive")
        )
        central_location = await session.scalar(
            select(Location).where(Location.kind == "central_office")
        )
        assert archive_location is not None
        assert central_location is not None

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
        admin = User(
            username=f"incident-admin-{suffix}", password_hash="test-hash",
            full_name="Incident Admin", role="admin", is_active=True,
        )
        viewer = User(
            username=f"incident-viewer-{suffix}", password_hash="test-hash",
            full_name="Incident Viewer", role="viewer", is_active=True,
        )
        branch_user = User(
            username=f"incident-branch-{suffix}", password_hash="test-hash",
            full_name="Incident Branch User", role="branch", branch_id=branch_id,
            is_active=True,
        )
        archive_user = User(
            username=f"incident-archive-{suffix}", password_hash="test-hash",
            full_name="Incident Reporter", role="archive", is_active=True,
        )
        central_user = User(
            username=f"incident-central-{suffix}", password_hash="test-hash",
            full_name="Central Incident Receiver", role="central_office", is_active=True,
        )
        courier = Courier(full_name=f"Incident Courier {suffix}", is_active=True)
        session.add_all([
            branch, branch_location, admin, viewer, branch_user, archive_user,
            central_user, courier,
        ])
        await session.flush()
        branch_location_id = branch_location.id
        branch_user_id = branch_user.id
        courier_id = courier.id
        archive_user_id = archive_user.id
        archive_location_id = archive_location.id
        central_location_id = central_location.id

        first_card_id, second_card_id = sorted((uuid.uuid4(), uuid.uuid4()), key=str)
        card = Card(
            id=first_card_id, card_number=f"INC-CARD-{suffix}",
            full_name="Incident Test Patient", date_of_birth=date(1980, 1, 2),
            origin_branch_id=branch.id, current_location_id=branch_location_id,
            status="active",
        )
        second_card = Card(
            id=second_card_id, card_number=f"INC-CARD-SECOND-{suffix}",
            full_name="Second Incident Test Patient", date_of_birth=date(1982, 2, 3),
            origin_branch_id=branch.id, current_location_id=branch_location_id,
            status="active",
        )
        session.add_all([card, second_card])
        await session.flush()

        workflow = PostgreSQLShipmentWorkflow(session)
        box = await workflow.create_box(
            location_id=branch_location_id, actor_id=branch_user_id,
            card_ids=[card.id, second_card.id],
        )
        shipment = await workflow.create_draft(
            from_location_id=branch_location_id,
            to_location_id=archive_location_id,
            courier_id=courier_id,
            issued_by=branch_user_id,
        )
        await workflow.add_box(
            shipment_id=shipment.id, box_id=box.id, actor_id=branch_user_id,
        )
        await workflow.hand_over(shipment_id=shipment.id, actor_id=branch_user_id)
        await workflow.register_exception(
            shipment_id=shipment.id, actor_id=archive_user_id,
            note="Интеграционная проверка инцидента",
        )
        await session.flush()

        admin_token = get_auth().create_session_token(admin)
        viewer_token = get_auth().create_session_token(viewer)
        archive_token = get_auth().create_session_token(archive_user)
        branch_token = get_auth().create_session_token(branch_user)
        central_token = get_auth().create_session_token(central_user)
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
                assert "Second Incident Test Patient" in detail.text
                assert f"INC-CARD-{suffix}" in detail.text
                assert f"INC-CARD-SECOND-{suffix}" in detail.text
                assert box_number in detail.text

                missing = await client.get(f"/admin/incidents/{uuid.uuid4()}")
                assert missing.status_code == 404

                client.cookies.set("session", viewer_token)
                forbidden = await client.get("/admin/incidents")
                assert forbidden.status_code == 403
                forbidden_detail = await client.get(f"/admin/incidents/{shipment_id}")
                assert forbidden_detail.status_code == 403

                client.cookies.set("session", branch_token)
                wrong_recipient = await client.post(
                    "/receive/resolve",
                    data={"shipment_id": str(shipment_id), "confirmed": "true",
                          "resolution_note": "Попытка постороннего филиала"},
                )
                assert wrong_recipient.status_code == 403

                client.cookies.set("session", archive_token)
                receive_page = await client.get("/receive")
                assert receive_page.status_code == 200
                assert "Инциденты, ожидающие проверки" in receive_page.text
                assert "Интеграционная проверка инцидента" in receive_page.text
                assert f"INC-CARD-{suffix}" in receive_page.text
                assert f"INC-CARD-SECOND-{suffix}" in receive_page.text

                blank_result = await client.post(
                    "/receive/resolve",
                    data={"shipment_id": str(shipment_id), "confirmed": "true",
                          "resolution_note": "   "},
                )
                assert blank_result.status_code == 200
                assert "Укажите результат проверки" in blank_result.text
                await session.refresh(shipment)
                assert shipment.status == "exception"

                # A resolution must not partially receive a shipment when even
                # one item from the box is absent from the persisted manifest.
                manifest_row = await session.scalar(select(ShipmentCard).where(
                    ShipmentCard.shipment_id == shipment_id,
                    ShipmentCard.card_id == card.id,
                ))
                assert manifest_row is not None
                # PostgreSQL rejects any attempt to alter a handed-over
                # manifest, so an incomplete manifest cannot be introduced
                # between incident registration and resolution.
                with pytest.raises(IntegrityError, match="Состав отправления можно менять только в черновике"):
                    async with session.begin_nested():
                        await session.delete(manifest_row)
                        await session.flush()

                await session.refresh(shipment)
                await session.refresh(card)
                await session.refresh(box)
                persisted_manifest_rows = list((await session.scalars(
                    select(ShipmentCard).where(ShipmentCard.shipment_id == shipment_id)
                )).all())
                assert {row.card_id for row in persisted_manifest_rows} == {
                    card.id, second_card.id,
                }
                assert shipment.status == "exception"
                assert card.current_shipment_id == shipment_id
                assert second_card.current_shipment_id == shipment_id
                assert box.current_shipment_id == shipment_id
                assert card.current_location_id is None
                assert box.current_location_id is None
                assert await session.scalar(select(DomainEvent.id).where(
                    DomainEvent.event_type == "shipment.exception_resolved",
                    DomainEvent.aggregate_id == shipment_id,
                )) is None

                # Even with an immutable manifest, one cargo item's live transit
                # pointer can be inconsistent. The first card is deliberately
                # ordered before the corrupted second card; resolution must reject
                # the whole shipment rather than partially receive the first card.
                with pytest.raises(
                    ShipmentWorkflowError,
                    match="Состояние карты не соответствует отправлению",
                ):
                    async with session.begin_nested():
                        second_card.current_location_id = archive_location_id
                        second_card.current_shipment_id = None
                        await session.flush()
                        await workflow.resolve_exception(
                            shipment_id=shipment_id,
                            actor_id=archive_user_id,
                            resolution_note="Попытка приёмки с несовпадающим состоянием карты",
                        )

                await session.refresh(card)
                await session.refresh(second_card)
                await session.refresh(box)
                await session.refresh(shipment)
                assert card.current_location_id is None
                assert card.current_shipment_id == shipment_id
                assert second_card.current_location_id is None
                assert second_card.current_shipment_id == shipment_id
                assert box.current_location_id is None
                assert box.current_shipment_id == shipment_id
                assert shipment.status == "exception"

                resolved = await client.post(
                    "/receive/resolve",
                    data={"shipment_id": str(shipment_id), "confirmed": "true",
                          "resolution_note": "Коробка и полный состав сверены с манифестом"},
                    follow_redirects=False,
                )
                assert resolved.status_code == 303
                assert resolved.headers["location"].startswith("/receive?message=")
                await session.refresh(shipment)
                assert shipment.status == "received"
                assert shipment.exception_note == "Интеграционная проверка инцидента"
                event = await session.scalar(select(DomainEvent).where(
                    DomainEvent.event_type == "shipment.exception_resolved",
                    DomainEvent.aggregate_id == shipment_id,
                ))
                assert event is not None
                assert event.payload["resolution_note"] == "Коробка и полный состав сверены с манифестом"

                # Resolving the same incident twice must not create a second
                # receipt or duplicate the immutable resolution event.
                duplicate_resolution = await client.post(
                    "/receive/resolve",
                    data={
                        "shipment_id": str(shipment_id),
                        "confirmed": "true",
                        "resolution_note": "Повторная попытка разрешения",
                    },
                )
                assert duplicate_resolution.status_code == 200
                assert "Инцидент не найден или уже разрешён" in duplicate_resolution.text
                await session.refresh(shipment)
                assert shipment.status == "received"
                assert await session.scalar(select(func.count()).select_from(DomainEvent).where(
                    DomainEvent.event_type == "shipment.exception_resolved",
                    DomainEvent.aggregate_id == shipment_id,
                )) == 1
                await session.refresh(card)
                await session.refresh(second_card)
                assert card.current_location_id == archive_location_id
                assert card.current_shipment_id is None
                assert second_card.current_location_id == archive_location_id
                assert second_card.current_shipment_id is None

                # The Central Office is also a receiving location and must be
                # able to inspect and resolve incidents on branch shipments.
                central_card = Card(
                    card_number=f"INC-CENTRAL-{suffix}",
                    full_name="Central Incident Patient",
                    date_of_birth=date(1985, 3, 4),
                    origin_branch_id=branch_id,
                    current_location_id=branch_location_id,
                    status="active",
                )
                session.add(central_card)
                await session.flush()
                central_box = await workflow.create_box(
                    location_id=branch_location_id,
                    actor_id=branch_user_id,
                    card_ids=[central_card.id],
                )
                central_shipment = await workflow.create_draft(
                    from_location_id=branch_location_id,
                    to_location_id=central_location_id,
                    courier_id=courier_id,
                    issued_by=branch_user_id,
                )
                await workflow.add_box(
                    shipment_id=central_shipment.id,
                    box_id=central_box.id,
                    actor_id=branch_user_id,
                )
                await workflow.hand_over(
                    shipment_id=central_shipment.id, actor_id=branch_user_id,
                )
                await workflow.register_exception(
                    shipment_id=central_shipment.id,
                    actor_id=branch_user_id,
                    note="Инцидент отправления в центральный офис",
                )
                await session.flush()
                central_shipment_id = central_shipment.id
                client.cookies.set("session", central_token)
                central_page = await client.get("/central/receive")
                assert central_page.status_code == 200
                assert "Инцидент отправления в центральный офис" in central_page.text
                assert f"INC-CENTRAL-{suffix}" in central_page.text

                wrong_recipient = await client.post(
                    "/receive/resolve",
                    data={
                        "shipment_id": str(central_shipment_id),
                        "confirmed": "true",
                        "resolution_note": "Попытка использовать маршрут архива",
                    },
                )
                assert wrong_recipient.status_code == 403

                central_resolved = await client.post(
                    "/central/receive/resolve",
                    data={
                        "shipment_id": str(central_shipment_id),
                        "confirmed": "true",
                        "resolution_note": "Коробка и карта проверены в ЦО",
                    },
                    follow_redirects=False,
                )
                assert central_resolved.status_code == 303
                assert central_resolved.headers["location"].startswith("/central/receive?message=")
                await session.refresh(central_shipment)
                await session.refresh(central_card)
                await session.refresh(central_box)
                assert central_shipment.status == "received"
                assert central_card.current_location_id == central_location_id
                assert central_card.current_shipment_id is None
                assert central_box.current_location_id == central_location_id
                assert central_box.current_shipment_id is None
                central_event = await session.scalar(select(DomainEvent).where(
                    DomainEvent.event_type == "shipment.exception_resolved",
                    DomainEvent.aggregate_id == central_shipment_id,
                ))
                assert central_event is not None
                assert central_event.payload["resolution_note"] == "Коробка и карта проверены в ЦО"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await outer_transaction.rollback()
            await connection.close()
    await engine.dispose()