"""PostgreSQL integration tests for atomic shipment handover and receipt."""
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from medarchive.adapters.postgresql.models import (
    ArchiveRequest, Box, BoxCardMembership, BoxCycle, Branch, Card, Courier,
    Location, ShipmentCard, User,
)
from medarchive.services.postgresql_shipment_workflow import (
    PostgreSQLShipmentWorkflow, ShipmentWorkflowError,
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


async def seed_locations(session):
    branch_id = uuid.uuid4()
    branch = Branch(id=branch_id, code=f"T-{branch_id.hex[:10]}", name="Test branch")
    branch_location = Location(kind="branch", branch_id=branch_id, name="Test branch")
    archive_location = Location(kind="archive", branch_id=None, name="Archive")
    central_location = Location(kind="central_office", branch_id=None, name="Central office")
    branch_user = User(
        username=f"branch-{uuid.uuid4().hex}", password_hash="x",
        full_name="Branch user", role="branch", branch_id=branch_id,
    )
    archive_user = User(
        username=f"archive-{uuid.uuid4().hex}", password_hash="x",
        full_name="Archive user", role="archive", branch_id=None,
    )
    central_user = User(
        username=f"central-{uuid.uuid4().hex}", password_hash="x",
        full_name="Central user", role="central_office", branch_id=None,
    )
    courier = Courier(full_name="Test courier")
    session.add_all([
        branch, branch_location, archive_location, central_location,
        branch_user, archive_user, central_user, courier,
    ])
    await session.flush()
    return branch, branch_location, archive_location, central_location, branch_user, archive_user, central_user, courier


@pytest.mark.asyncio
async def test_branch_box_shipment_is_handed_over_and_received_atomically(pg_session):
    (
        branch, branch_loc, archive_loc, _central_loc,
        branch_user, archive_user, _central_user, courier,
    ) = await seed_locations(pg_session)

    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Test patient",
        date_of_birth=__import__("datetime").date(1980, 1, 1),
        origin_branch_id=branch.id, current_location_id=branch_loc.id,
    )
    box = Box(
        box_number=f"BX-{uuid.uuid4().hex[:12]}", current_location_id=branch_loc.id,
        created_by=branch_user.id,
    )
    pg_session.add_all([card, box])
    await pg_session.flush()
    cycle = BoxCycle(box_id=box.id, cycle_number=1, started_by=branch_user.id)
    pg_session.add(cycle)
    await pg_session.flush()
    pg_session.add(BoxCardMembership(box_cycle_id=cycle.id, card_id=card.id, added_by=branch_user.id))
    await pg_session.flush()

    workflow = PostgreSQLShipmentWorkflow(pg_session)
    shipment = await workflow.create_draft(
        from_location_id=branch_loc.id, to_location_id=archive_loc.id,
        courier_id=courier.id, issued_by=branch_user.id,
    )
    await workflow.add_box(shipment_id=shipment.id, box_id=box.id, actor_id=branch_user.id)
    await workflow.hand_over(shipment_id=shipment.id, actor_id=branch_user.id)

    await pg_session.refresh(box)
    await pg_session.refresh(card)
    assert box.current_location_id is None
    assert box.current_shipment_id == shipment.id
    assert card.current_location_id is None
    assert card.current_shipment_id == shipment.id

    manifest_row = await pg_session.scalar(
        select(ShipmentCard).where(
            ShipmentCard.shipment_id == shipment.id,
            ShipmentCard.card_id == card.id,
        )
    )
    assert manifest_row is not None and manifest_row.box_id == box.id

    await workflow.receive(shipment_id=shipment.id, actor_id=archive_user.id)
    await pg_session.refresh(box)
    await pg_session.refresh(card)
    await pg_session.refresh(shipment)
    assert shipment.status == "received"
    assert box.current_location_id == archive_loc.id
    assert box.current_shipment_id is None
    assert card.current_location_id == archive_loc.id
    assert card.current_shipment_id is None


@pytest.mark.asyncio
async def test_archive_to_central_requires_request_and_completes_it_on_receipt(pg_session):
    (
        branch, _branch_loc, archive_loc, central_loc,
        _branch_user, archive_user, central_user, courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Requested patient",
        date_of_birth=__import__("datetime").date(1975, 2, 3),
        origin_branch_id=branch.id, current_location_id=archive_loc.id,
    )
    pg_session.add(card)
    await pg_session.flush()
    request = ArchiveRequest(card_id=card.id, requested_by=central_user.id, status="new")
    pg_session.add(request)
    await pg_session.flush()

    workflow = PostgreSQLShipmentWorkflow(pg_session)
    shipment = await workflow.create_draft(
        from_location_id=archive_loc.id, to_location_id=central_loc.id,
        courier_id=courier.id, issued_by=archive_user.id,
    )
    await workflow.add_standalone_card(
        shipment_id=shipment.id, card_id=card.id, actor_id=archive_user.id,
    )
    await workflow.hand_over(shipment_id=shipment.id, actor_id=archive_user.id)
    await workflow.receive(shipment_id=shipment.id, actor_id=central_user.id)

    await pg_session.refresh(card)
    await pg_session.refresh(request)
    assert card.current_location_id == central_loc.id
    assert card.current_shipment_id is None
    assert request.status == "completed"
    assert request.fulfilled_by == central_user.id


@pytest.mark.asyncio
async def test_archive_cannot_send_unrequested_card_to_central_office(pg_session):
    (
        branch, _branch_loc, archive_loc, central_loc,
        _branch_user, archive_user, _central_user, courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Unrequested patient",
        date_of_birth=__import__("datetime").date(1990, 4, 5),
        origin_branch_id=branch.id, current_location_id=archive_loc.id,
    )
    pg_session.add(card)
    await pg_session.flush()

    workflow = PostgreSQLShipmentWorkflow(pg_session)
    shipment = await workflow.create_draft(
        from_location_id=archive_loc.id, to_location_id=central_loc.id,
        courier_id=courier.id, issued_by=archive_user.id,
    )
    await workflow.add_standalone_card(
        shipment_id=shipment.id, card_id=card.id, actor_id=archive_user.id,
    )
    with pytest.raises(ShipmentWorkflowError, match="заявка ЦО"):
        await workflow.hand_over(shipment_id=shipment.id, actor_id=archive_user.id)
    await pg_session.refresh(card)
    assert card.current_location_id == archive_loc.id
    assert card.current_shipment_id is None


@pytest.mark.asyncio
async def test_archive_can_pack_multiple_requested_cards_and_central_can_return_box(pg_session):
    (
        branch, _branch_loc, archive_loc, central_loc,
        _branch_user, archive_user, central_user, courier,
    ) = await seed_locations(pg_session)
    cards = [
        Card(
            card_number=f"C-{uuid.uuid4().hex}", full_name=f"Requested patient {n}",
            date_of_birth=__import__("datetime").date(1980 + n, 1, 1),
            origin_branch_id=branch.id, current_location_id=archive_loc.id,
        )
        for n in (1, 2)
    ]
    pg_session.add_all(cards)
    await pg_session.flush()
    requests = [
        ArchiveRequest(card_id=card.id, requested_by=central_user.id, status="new")
        for card in cards
    ]
    pg_session.add_all(requests)
    await pg_session.flush()

    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(
        location_id=archive_loc.id,
        actor_id=archive_user.id,
        card_ids=[card.id for card in cards],
    )
    outbound = await workflow.create_draft(
        from_location_id=archive_loc.id, to_location_id=central_loc.id,
        courier_id=courier.id, issued_by=archive_user.id,
    )
    await workflow.add_box(shipment_id=outbound.id, box_id=box.id, actor_id=archive_user.id)
    await workflow.hand_over(shipment_id=outbound.id, actor_id=archive_user.id)
    await workflow.receive(shipment_id=outbound.id, actor_id=central_user.id)

    for card, request in zip(cards, requests):
        await pg_session.refresh(card)
        await pg_session.refresh(request)
        assert card.current_location_id == central_loc.id
        assert request.status == "completed"

    # The same physical box can be reused for the return route after receipt.
    return_shipment = await workflow.create_draft(
        from_location_id=central_loc.id, to_location_id=archive_loc.id,
        courier_id=courier.id, issued_by=central_user.id,
    )
    await workflow.add_box(
        shipment_id=return_shipment.id, box_id=box.id, actor_id=central_user.id,
    )
    await workflow.hand_over(shipment_id=return_shipment.id, actor_id=central_user.id)
    await workflow.receive(shipment_id=return_shipment.id, actor_id=archive_user.id)
    await pg_session.refresh(box)
    assert box.current_location_id == archive_loc.id
    assert box.current_shipment_id is None
    for card in cards:
        await pg_session.refresh(card)
        assert card.current_location_id == archive_loc.id
        assert card.current_shipment_id is None


@pytest.mark.asyncio
async def test_admin_cannot_perform_ordinary_logistics(pg_session):
    (
        _branch, branch_loc, archive_loc, _central_loc,
        _branch_user, _archive_user, _central_user, courier,
    ) = await seed_locations(pg_session)
    admin = User(
        username=f"admin-{uuid.uuid4().hex}", password_hash="x",
        full_name="Administrator", role="admin", branch_id=None,
    )
    pg_session.add(admin)
    await pg_session.flush()

    workflow = PostgreSQLShipmentWorkflow(pg_session)
    with pytest.raises(ShipmentWorkflowError, match="Недостаточно прав"):
        await workflow.create_draft(
            from_location_id=branch_loc.id, to_location_id=archive_loc.id,
            courier_id=courier.id, issued_by=admin.id,
        )


@pytest.mark.asyncio
async def test_receipt_is_blocked_without_changing_transit_state_when_box_manifest_differs(pg_session):
    (
        branch, branch_loc, archive_loc, _central_loc,
        branch_user, archive_user, _central_user, courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Mismatch patient",
        date_of_birth=__import__("datetime").date(1988, 6, 7),
        origin_branch_id=branch.id, current_location_id=branch_loc.id,
    )
    box = Box(
        box_number=f"BX-{uuid.uuid4().hex[:16]}",
        current_location_id=branch_loc.id, created_by=branch_user.id,
    )
    pg_session.add_all([card, box])
    await pg_session.flush()
    cycle = BoxCycle(box_id=box.id, cycle_number=1, started_by=branch_user.id)
    pg_session.add(cycle)
    await pg_session.flush()
    membership = BoxCardMembership(
        box_cycle_id=cycle.id, card_id=card.id, added_by=branch_user.id,
    )
    pg_session.add(membership)
    await pg_session.flush()

    workflow = PostgreSQLShipmentWorkflow(pg_session)
    shipment = await workflow.create_draft(
        from_location_id=branch_loc.id, to_location_id=archive_loc.id,
        courier_id=courier.id, issued_by=branch_user.id,
    )
    await workflow.add_box(shipment_id=shipment.id, box_id=box.id, actor_id=branch_user.id)
    await workflow.hand_over(shipment_id=shipment.id, actor_id=branch_user.id)

    # Simulate an inconsistent membership record; ordinary receipt must stop.
    membership.removed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    await pg_session.flush()
    with pytest.raises(ShipmentWorkflowError, match="Состав"):
        await workflow.receive(shipment_id=shipment.id, actor_id=archive_user.id)

    await pg_session.refresh(shipment)
    await pg_session.refresh(box)
    await pg_session.refresh(card)
    assert shipment.status == "in_transit"
    assert box.current_shipment_id == shipment.id
    assert card.current_shipment_id == shipment.id
    assert box.current_location_id is None
    assert card.current_location_id is None
