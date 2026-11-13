"""Regression tests for archive shelf cargo guards at courier handover."""
import os
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from medarchive.adapters.postgresql.models import (
    ArchiveShelf,
    Box,
    BoxPlacement,
    Branch,
    Card,
    CardPlacement,
    Courier,
    DomainEvent,
    Location,
    Shipment,
    ShipmentBox,
    ShipmentCard,
    User,
)
from medarchive.services.postgresql_archive_workflow import PostgreSQLArchiveWorkflow
from medarchive.services.postgresql_shipment_workflow import (
    PostgreSQLShipmentWorkflow,
    ShipmentWorkflowError,
)


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.fixture
async def pg_session():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        transaction = await session.begin()
        try:
            yield session
        finally:
            await transaction.rollback()
    await engine.dispose()


async def seed_archive_cargo(session):
    branch_id = uuid.uuid4()
    branch = Branch(
        id=branch_id, code=f"SHELF-{branch_id.hex[:10]}",
        name="Shelf guard origin", is_active=True,
    )
    archive = Location(
        kind="archive", branch_id=None, name="Shelf guard archive", is_active=True,
    )
    central = Location(
        kind="central_office", branch_id=None, name="Shelf guard central", is_active=True,
    )
    actor = User(
        username=f"shelf-archive-{uuid.uuid4().hex[:12]}",
        password_hash="x", full_name="Archive shelf operator",
        role="archive", branch_id=None, is_active=True,
    )
    courier = Courier(full_name="Shelf guard courier", is_active=True)
    shelf = ArchiveShelf(
        section_name=f"SG-{uuid.uuid4().hex[:6]}",
        shelf_name=f"S-{uuid.uuid4().hex[:6]}",
        qr_code=f"QR-{uuid.uuid4().hex}", is_active=True,
    )
    card = Card(
        card_number=f"SHELF-CARD-{uuid.uuid4().hex[:10]}",
        full_name="Shelf guard patient", date_of_birth=date(1980, 1, 1),
        origin_branch_id=branch_id, current_location_id=archive.id,
        current_shipment_id=None, status="active", custody_status="available",
    )
    session.add_all([branch, archive, central, actor, courier, shelf])
    await session.flush()
    session.add(card)
    await session.flush()
    return archive, central, actor, courier, shelf, card


@pytest.mark.asyncio
async def test_shelved_box_cannot_be_added_or_handed_over(pg_session):
    archive, central, actor, courier, shelf, card = await seed_archive_cargo(pg_session)
    shipping = PostgreSQLShipmentWorkflow(pg_session)
    archive_workflow = PostgreSQLArchiveWorkflow(pg_session)

    box = await shipping.create_box(
        location_id=archive.id, actor_id=actor.id, card_ids=[card.id],
    )
    placement = await archive_workflow.place_box(
        box_id=box.id, shelf_id=shelf.id, actor_id=actor.id,
    )
    shipment = await shipping.create_draft(
        from_location_id=archive.id, to_location_id=central.id,
        courier_id=courier.id, issued_by=actor.id,
    )

    with pytest.raises(ShipmentWorkflowError, match="снимите коробку с архивной полки"):
        await shipping.add_box(
            shipment_id=shipment.id, box_id=box.id, actor_id=actor.id,
        )

    # Simulate a legacy/stale manifest created before the box was shelved.
    pg_session.add(ShipmentBox(shipment_id=shipment.id, box_id=box.id))
    await pg_session.flush()
    events_before = await pg_session.scalar(select(func.count()).select_from(DomainEvent))

    with pytest.raises(ShipmentWorkflowError, match="пока она размещена на архивной полке"):
        await shipping.hand_over(shipment_id=shipment.id, actor_id=actor.id)

    await pg_session.refresh(shipment)
    await pg_session.refresh(box)
    await pg_session.refresh(placement)
    assert shipment.status == "draft"
    assert shipment.handed_over_at is None
    assert box.current_location_id == archive.id
    assert box.current_shipment_id is None
    assert placement.removed_at is None
    assert await pg_session.scalar(select(func.count()).select_from(DomainEvent)) == events_before


@pytest.mark.asyncio
async def test_shelved_standalone_card_cannot_be_added_or_handed_over(pg_session):
    archive, central, actor, courier, shelf, card = await seed_archive_cargo(pg_session)
    shipping = PostgreSQLShipmentWorkflow(pg_session)
    archive_workflow = PostgreSQLArchiveWorkflow(pg_session)
    placement = await archive_workflow.place_card(
        card_id=card.id, shelf_id=shelf.id, actor_id=actor.id,
    )
    shipment = await shipping.create_draft(
        from_location_id=archive.id, to_location_id=central.id,
        courier_id=courier.id, issued_by=actor.id,
    )

    with pytest.raises(ShipmentWorkflowError, match="снимите карту с архивной полки"):
        await shipping.add_standalone_card(
            shipment_id=shipment.id, card_id=card.id, actor_id=actor.id,
        )

    # Simulate a legacy/stale manifest created before the card was shelved.
    pg_session.add(ShipmentCard(
        shipment_id=shipment.id, card_id=card.id, box_id=None,
    ))
    await pg_session.flush()
    events_before = await pg_session.scalar(select(func.count()).select_from(DomainEvent))

    with pytest.raises(ShipmentWorkflowError, match="пока она размещена на архивной полке"):
        await shipping.hand_over(shipment_id=shipment.id, actor_id=actor.id)

    await pg_session.refresh(shipment)
    await pg_session.refresh(card)
    await pg_session.refresh(placement)
    assert shipment.status == "draft"
    assert shipment.handed_over_at is None
    assert card.current_location_id == archive.id
    assert card.current_shipment_id is None
    assert placement.removed_at is None
    assert await pg_session.scalar(select(func.count()).select_from(DomainEvent)) == events_before
