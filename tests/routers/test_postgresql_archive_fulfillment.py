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
    Location,
    Shipment,
    ShipmentBox,
    ShipmentCard,
    User,
)
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app


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
    unrequested_card = make_card(unrequested_card_id, "NO-REQ-AF")

    try:
        async with session.begin():
            session.add_all([
                branch, archive, central, central_actor, archive_actor, courier,
            ])
            await session.flush()
            session.add_all([requested_card, unrequested_card])

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
                data={"card_ids": str(requested_card_id)},
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
            request_row = await session.scalar(
                select(ArchiveRequest).where(ArchiveRequest.card_id == requested_card_id)
            )
            assert request_row is not None
            assert request_row.status == "new"
            assert request_row.fulfillment_shipment_id is None
            assert await session.scalar(select(Shipment.id)) is None

            client.cookies.set("session", archive_token)
            dispatched = await client.post(
                "/requests/fulfill",
                data={
                    "card_ids": [str(requested_card_id)],
                    "courier_id": str(courier_id),
                },
            )
            assert dispatched.status_code == 303

            request_row = await session.scalar(
                select(ArchiveRequest).where(ArchiveRequest.card_id == requested_card_id)
            )
            assert request_row is not None
            assert request_row.status == "in_progress"
            assert request_row.fulfillment_shipment_id is not None
            shipment_id = request_row.fulfillment_shipment_id

            shipment = await session.get(Shipment, shipment_id)
            assert shipment is not None
            assert shipment.status == "in_transit"
            assert shipment.from_location_id == archive_location_id
            assert shipment.to_location_id == central_location_id
            assert await session.scalar(
                select(ShipmentBox.box_id).where(ShipmentBox.shipment_id == shipment_id)
            ) is None
            manifest_row = await session.scalar(
                select(ShipmentCard).where(
                    ShipmentCard.shipment_id == shipment_id,
                    ShipmentCard.card_id == requested_card_id,
                )
            )
            assert manifest_row is not None
            assert manifest_row.box_id is None

            in_transit_card = await session.get(Card, requested_card_id)
            assert in_transit_card is not None
            assert in_transit_card.current_location_id is None
            assert in_transit_card.current_shipment_id == shipment_id

            client.cookies.set("session", central_token)
            central_receive = await client.get(
            )
            assert central_receive.status_code == 200
            assert requested_card.card_number in central_receive.text

            client.cookies.set("session", central_token)
            received = await client.post(
                "/central/receive/confirm",
                data={"shipment_id": str(shipment_id), "confirmed": "true"},
            )
            assert received.status_code == 303

            shipment = await session.get(Shipment, shipment_id)
            request_row = await session.get(ArchiveRequest, request_row.id)
            card = await session.get(Card, requested_card_id)
            assert shipment is not None and shipment.status == "received"
            assert request_row is not None and request_row.status == "completed"
            assert request_row.fulfilled_by == central_actor_id
            assert request_row.completed_at is not None
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
