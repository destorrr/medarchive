"""End-to-end PostgreSQL archive request, dispatch and central receipt workflow."""

import os
import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    ArchiveRequest,
    Branch,
    Card,
    Courier,
    DomainEvent,
    Location,
    Shipment,
    ShipmentBox,
    ShipmentCard,
    User,
)
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app
from medarchive.services.postgresql_shipment_workflow import (
    PostgreSQLShipmentWorkflow,
    ShipmentWorkflowError,
)


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_archive_request_is_fulfilled_by_unboxed_shipment_and_completed_on_central_receipt():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(
        Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="postgres-archive-fulfillment-integration-secret",
        )
    )
    database = await get_pg_database()
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    async def session_override():
        yield session

    app.dependency_overrides[get_pg_session] = session_override

    branch_id = uuid.uuid4()
    archive_location_id = uuid.uuid4()
    central_location_id = uuid.uuid4()
    central_actor_id = uuid.uuid4()
    archive_actor_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    requested_card_id = uuid.uuid4()
    second_requested_card_id = uuid.uuid4()
    unrequested_card_id = uuid.uuid4()

    branch = Branch(
        id=branch_id,
        code=f"AF-{uuid.uuid4().hex[:10]}",
        name="Archive Fulfillment Origin",
        is_active=True,
    )
    archive = Location(
        id=archive_location_id, kind="archive", branch_id=None,
        name="Archive Fulfillment", is_active=True,
    )
    central = Location(
        id=central_location_id, kind="central_office", branch_id=None,
        name="Central Fulfillment", is_active=True,
    )
    central_actor = User(
        id=central_actor_id,
        username=f"central-af-{uuid.uuid4().hex[:10]}",
        password_hash="test-only-hash",
        full_name="Central Fulfillment Operator",
        role="central_office",
        branch_id=None,
        is_active=True,
    )
    archive_actor = User(
        id=archive_actor_id,
        username=f"archive-af-{uuid.uuid4().hex[:10]}",
        password_hash="test-only-hash",
        full_name="Archive Fulfillment Operator",
        role="archive",
        branch_id=None,
        is_active=True,
    )
    courier = Courier(id=courier_id, full_name="Archive Fulfillment Courier", is_active=True)

    def make_card(card_id: uuid.UUID, prefix: str) -> Card:
        return Card(
            id=card_id,
            card_number=f"{prefix}-{uuid.uuid4().hex[:10]}",
            full_name=f"{prefix} Test Patient",
            date_of_birth=date(1980, 1, 2),
            origin_branch_id=branch_id,
            current_location_id=archive_location_id,
            current_shipment_id=None,
            status="active",
            custody_status="available",
        )

    requested_card = make_card(requested_card_id, "REQ-AF")
    second_requested_card = make_card(second_requested_card_id, "REQ-AF-2")
    unrequested_card = make_card(unrequested_card_id, "NO-REQ-AF")

    try:
        async with session.begin():
            session.add_all([
                branch, archive, central, central_actor, archive_actor, courier,
            ])
            await session.flush()
            session.add_all([requested_card, second_requested_card, unrequested_card])

        central_token = get_auth().create_session_token(central_actor)
        archive_token = get_auth().create_session_token(archive_actor)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("session", central_token)
            search = await client.get(
                "/requests/new",
                params={"query": requested_card.card_number},
            )
            assert search.status_code == 200
            assert requested_card.card_number in search.text

            client.cookies.set("session", central_token)
            created = await client.post(
                "/requests/create",
                data={"card_ids": [str(requested_card_id), str(second_requested_card_id)]},
            )
            assert created.status_code == 303

            # An unrequested card must block the entire dispatch transaction.
            client.cookies.set("session", archive_token)
            rejected = await client.post(
                "/requests/fulfill",
                data={
                    "card_ids": [str(requested_card_id), str(unrequested_card_id)],
                    "courier_id": str(courier_id),
                },
            )
            assert rejected.status_code == 200
            assert "свободная активная заявка ЦО" in rejected.text
            request_rows = list((await session.scalars(
                select(ArchiveRequest).where(
                    ArchiveRequest.card_id.in_([requested_card_id, second_requested_card_id])
                )
            )).all())
            assert len(request_rows) == 2
            assert all(row.status == "new" for row in request_rows)
            assert all(row.fulfillment_shipment_id is None for row in request_rows)
            assert await session.scalar(select(Shipment.id)) is None

            # The archive may use internal storage boxes, but it must never
            # dispatch a transport box to Central Office: fulfillment is unboxed.
            workflow = PostgreSQLShipmentWorkflow(session)
            archive_box = await workflow.create_box(
                location_id=archive_location_id,
                actor_id=archive_actor_id,
                card_ids=[unrequested_card_id],
            )
            forbidden_box_shipment = await workflow.create_draft(
                from_location_id=archive_location_id,
                to_location_id=central_location_id,
                courier_id=courier_id,
                issued_by=archive_actor_id,
            )
            with pytest.raises(
                ShipmentWorkflowError,
                match="архив отправляет только отдельные карты, без коробок",
            ):
                await workflow.add_box(
                    shipment_id=forbidden_box_shipment.id,
                    box_id=archive_box.id,
                    actor_id=archive_actor_id,
                )
            assert forbidden_box_shipment.status == "draft"
            assert await session.scalar(
                select(ShipmentBox.box_id).where(
                    ShipmentBox.shipment_id == forbidden_box_shipment.id
                )
            ) is None
            assert archive_box.current_location_id == archive_location_id
            assert archive_box.current_shipment_id is None
            assert unrequested_card.current_location_id == archive_location_id
            assert unrequested_card.current_shipment_id is None

            client.cookies.set("session", archive_token)
            dispatched = await client.post(
                "/requests/fulfill",
                data={
                    "card_ids": [str(requested_card_id), str(second_requested_card_id)],
                    "courier_id": str(courier_id),
                },
            )
            assert dispatched.status_code == 303

            request_rows = list((await session.scalars(
                select(ArchiveRequest).where(
                    ArchiveRequest.card_id.in_([requested_card_id, second_requested_card_id])
                )
            )).all())
            assert len(request_rows) == 2
            assert all(row.status == "in_progress" for row in request_rows)
            assert len({row.fulfillment_shipment_id for row in request_rows}) == 1
            assert all(row.fulfilled_by == archive_actor_id for row in request_rows)
            request_ids = [row.id for row in request_rows]
            started_events = list((await session.scalars(
                select(DomainEvent).where(
                    DomainEvent.event_type == "archive_request.fulfillment_started",
                    DomainEvent.aggregate_type == "archive_request",
                    DomainEvent.aggregate_id.in_(request_ids),
                )
            )).all())
            assert len(started_events) == 2
            assert {event.actor_user_id for event in started_events} == {archive_actor_id}
            assert {event.payload["shipment_id"] for event in started_events} == {
                str(row.fulfillment_shipment_id) for row in request_rows
            }
            shipment_id = request_rows[0].fulfillment_shipment_id
            assert shipment_id is not None

            shipment = await session.get(Shipment, shipment_id)
            assert shipment is not None
            assert shipment.status == "in_transit"
            assert shipment.from_location_id == archive_location_id
            assert shipment.to_location_id == central_location_id
            assert await session.scalar(
                select(ShipmentBox.box_id).where(ShipmentBox.shipment_id == shipment_id)
            ) is None
            manifest_rows = list((await session.scalars(
                select(ShipmentCard).where(ShipmentCard.shipment_id == shipment_id)
            )).all())
            assert {row.card_id for row in manifest_rows} == {
                requested_card_id, second_requested_card_id,
            }
            assert all(row.box_id is None for row in manifest_rows)

            for card_id in (requested_card_id, second_requested_card_id):
                in_transit_card = await session.get(Card, card_id)
                assert in_transit_card is not None
                assert in_transit_card.current_location_id is None
                assert in_transit_card.current_shipment_id == shipment_id

            client.cookies.set("session", central_token)
            central_receive = await client.get("/central/receive")
            assert central_receive.status_code == 200
            assert requested_card.card_number in central_receive.text
            assert second_requested_card.card_number in central_receive.text

            client.cookies.set("session", central_token)
            received = await client.post(
                "/central/receive/confirm",
                data={"shipment_id": str(shipment_id), "confirmed": "true"},
            )
            assert received.status_code == 303

            shipment = await session.get(Shipment, shipment_id)
            completed_requests = list((await session.scalars(
                select(ArchiveRequest).where(
                    ArchiveRequest.card_id.in_([requested_card_id, second_requested_card_id])
                )
            )).all())
            assert shipment is not None and shipment.status == "received"
            assert len(completed_requests) == 2
            assert all(row.status == "completed" for row in completed_requests)
            assert all(row.fulfilled_by == archive_actor_id for row in completed_requests)
            assert all(row.completed_at is not None for row in completed_requests)
            completed_events = list((await session.scalars(
                select(DomainEvent).where(
                    DomainEvent.event_type == "archive_request.completed",
                    DomainEvent.aggregate_type == "archive_request",
                    DomainEvent.aggregate_id.in_([row.id for row in completed_requests]),
                )
            )).all())
            assert len(completed_events) == 2
            assert {event.actor_user_id for event in completed_events} == {central_actor_id}
            assert {event.payload["shipment_id"] for event in completed_events} == {
                str(shipment_id)
            }
            for card_id in (requested_card_id, second_requested_card_id):
                card = await session.get(Card, card_id)
                assert card is not None
                assert card.current_location_id == central_location_id
                assert card.current_shipment_id is None
                assert card.custody_status == "available"

            # The unrelated card remains in the archive and was never dispatched.
            untouched_card = await session.get(Card, unrequested_card_id)
            assert untouched_card is not None
            assert untouched_card.current_location_id == archive_location_id
            assert untouched_card.current_shipment_id is None
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
