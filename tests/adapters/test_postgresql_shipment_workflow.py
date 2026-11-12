"""PostgreSQL integration tests for atomic shipment handover and receipt."""
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from medarchive.adapters.postgresql.models import (
    ArchiveRequest, ArchiveShelf, Box, BoxCardMembership, BoxCycle, Branch, Card,
    Courier, DomainEvent, Location, ShipmentCard, User,
)
from medarchive.services.postgresql_archive_workflow import (
    ArchiveWorkflowError, PostgreSQLArchiveWorkflow,
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
    branch_location = Location(kind="branch", branch_id=branch_id, name="Test branch", is_active=True)
    archive_location = Location(kind="archive", branch_id=None, name="Archive", is_active=True)
    central_location = Location(kind="central_office", branch_id=None, name="Central office", is_active=True)
    branch_user = User(
        username=f"branch-{uuid.uuid4().hex}", password_hash="x",
        full_name="Branch user", role="branch", branch_id=branch_id, is_active=True,
    )
    archive_user = User(
        username=f"archive-{uuid.uuid4().hex}", password_hash="x",
        full_name="Archive user", role="archive", branch_id=None, is_active=True,
    )
    central_user = User(
        username=f"central-{uuid.uuid4().hex}", password_hash="x",
        full_name="Central user", role="central_office", branch_id=None, is_active=True,
    )
    courier = Courier(full_name="Test courier", is_active=True)
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
        origin_branch_id=branch.id, current_location_id=branch_loc.id, status="active",
    )
    pg_session.add(card)
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(location_id=branch_loc.id, actor_id=branch_user.id)
    await workflow.add_card_to_box(
        box_id=box.id, card_id=card.id, actor_id=branch_user.id,
    )
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
        origin_branch_id=branch.id, current_location_id=archive_loc.id, status="active",
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
        origin_branch_id=branch.id, current_location_id=archive_loc.id, status="active",
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
            origin_branch_id=branch.id, current_location_id=archive_loc.id, status="active",
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
        full_name="Administrator", role="admin", branch_id=None, is_active=True,
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
async def test_database_freezes_box_membership_after_handover(pg_session):
    (
        branch, branch_loc, archive_loc, _central_loc,
        branch_user, archive_user, _central_user, courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Transit patient",
        date_of_birth=__import__("datetime").date(1988, 6, 7),
        origin_branch_id=branch.id, current_location_id=branch_loc.id, status="active",
    )
    pg_session.add(card)
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(location_id=branch_loc.id, actor_id=branch_user.id)
    await workflow.add_card_to_box(box_id=box.id, card_id=card.id, actor_id=branch_user.id)
    shipment = await workflow.create_draft(
        from_location_id=branch_loc.id, to_location_id=archive_loc.id,
        courier_id=courier.id, issued_by=branch_user.id,
    )
    await workflow.add_box(shipment_id=shipment.id, box_id=box.id, actor_id=branch_user.id)
    await workflow.hand_over(shipment_id=shipment.id, actor_id=branch_user.id)

    membership = await pg_session.scalar(
        select(BoxCardMembership).where(BoxCardMembership.card_id == card.id)
    )
    with pytest.raises(DBAPIError):
        async with pg_session.begin_nested():
            membership.removed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            membership.removed_by = archive_user.id
            await pg_session.flush()

    await pg_session.refresh(shipment)
    await pg_session.refresh(box)
    await pg_session.refresh(card)
    await pg_session.refresh(membership)
    assert shipment.status == "in_transit"
    assert membership.removed_at is None
    assert box.current_shipment_id == shipment.id
    assert card.current_shipment_id == shipment.id


@pytest.mark.asyncio
async def test_database_freezes_route_and_manifest_after_handover(pg_session):
    (
        branch, branch_loc, archive_loc, central_loc,
        branch_user, _archive_user, _central_user, courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Immutable manifest patient",
        date_of_birth=__import__("datetime").date(1981, 2, 3),
        origin_branch_id=branch.id, current_location_id=branch_loc.id, status="active",
    )
    another_courier = Courier(full_name="Second courier", is_active=True)
    pg_session.add_all([card, another_courier])
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    shipment = await workflow.create_draft(
        from_location_id=branch_loc.id, to_location_id=archive_loc.id,
        courier_id=courier.id, issued_by=branch_user.id,
    )
    await workflow.add_standalone_card(
        shipment_id=shipment.id, card_id=card.id, actor_id=branch_user.id,
    )
    await workflow.hand_over(shipment_id=shipment.id, actor_id=branch_user.id)
    shipment_id = shipment.id
    card_id = card.id

    with pytest.raises(DBAPIError):
        async with pg_session.begin_nested():
            shipment.to_location_id = central_loc.id
            shipment.courier_id = another_courier.id
            await pg_session.flush()

    with pytest.raises(DBAPIError):
        async with pg_session.begin_nested():
            pg_session.add(ShipmentCard(
                shipment_id=shipment_id, card_id=card_id, box_id=None,
            ))
            await pg_session.flush()

    event = await pg_session.scalar(
        select(DomainEvent).where(
            DomainEvent.aggregate_type == "shipment",
            DomainEvent.aggregate_id == shipment_id,
        ).limit(1)
    )
    with pytest.raises(DBAPIError):
        async with pg_session.begin_nested():
            event.payload = {"tampered": True}
            await pg_session.flush()


@pytest.mark.asyncio
async def test_central_office_can_request_archive_cards_and_archive_can_repack_them(pg_session):
    (
        branch, _branch_loc, archive_loc, _central_loc,
        _branch_user, archive_user, central_user, _courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Archive request patient",
        date_of_birth=__import__("datetime").date(1979, 5, 6),
        origin_branch_id=branch.id, current_location_id=archive_loc.id, status="active",
    )
    pg_session.add(card)
    await pg_session.flush()
    archive = PostgreSQLArchiveWorkflow(pg_session)
    with pytest.raises(ArchiveWorkflowError, match="прав"):
        await archive.create_requests(card_ids=[card.id], actor_id=archive_user.id)

    requests = await archive.create_requests(
        card_ids=[card.id], actor_id=central_user.id,
    )
    assert len(requests) == 1 and requests[0].status == "new"

    shipping = PostgreSQLShipmentWorkflow(pg_session)
    original_box = await shipping.create_box(
        location_id=archive_loc.id, actor_id=archive_user.id,
        card_ids=[card.id],
    )
    shelf = ArchiveShelf(
        section_name="A", shelf_name=f"S-{uuid.uuid4().hex[:8]}", is_active=True,
    )
    pg_session.add(shelf)
    await pg_session.flush()
    placement = await archive.place_box(
        box_id=original_box.id, shelf_id=shelf.id, actor_id=archive_user.id,
    )
    assert placement.removed_at is None

    await archive.remove_box_from_shelf(box_id=original_box.id, actor_id=archive_user.id)
    await shipping.remove_card_from_box(
        box_id=original_box.id, card_id=card.id, actor_id=archive_user.id,
    )
    await shipping.add_card_to_box(
        box_id=original_box.id, card_id=card.id, actor_id=archive_user.id,
    )
    await shipping.remove_card_from_box(
        box_id=original_box.id, card_id=card.id, actor_id=archive_user.id,
    )
    cycles = list((await pg_session.scalars(
        select(BoxCycle).where(BoxCycle.box_id == original_box.id).order_by(BoxCycle.cycle_number)
    )).all())
    assert [cycle.cycle_number for cycle in cycles] == [1, 2]
    assert all(cycle.ended_at is not None for cycle in cycles)

    new_box = await shipping.create_box(
        location_id=archive_loc.id, actor_id=archive_user.id,
        card_ids=[card.id],
    )
    assert new_box.id != original_box.id
    await pg_session.refresh(requests[0])
    assert requests[0].status == "new"
    assert requests[0].fulfillment_shipment_id is None


@pytest.mark.asyncio
async def test_archive_can_mark_requested_card_not_found_with_reason(pg_session):
    (
        branch, _branch_loc, archive_loc, _central_loc,
        _branch_user, archive_user, central_user, _courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Missing archive patient",
        date_of_birth=__import__("datetime").date(1977, 7, 8),
        origin_branch_id=branch.id, current_location_id=archive_loc.id, status="active",
    )
    pg_session.add(card)
    await pg_session.flush()
    archive = PostgreSQLArchiveWorkflow(pg_session)
    request = (await archive.create_requests(
        card_ids=[card.id], actor_id=central_user.id,
    ))[0]
    with pytest.raises(ArchiveWorkflowError, match="пояснение"):
        await archive.mark_not_found(
            request_id=request.id, actor_id=archive_user.id, note="  ",
        )
    await archive.mark_not_found(
        request_id=request.id, actor_id=archive_user.id,
        note="Не обнаружена на проверенных стеллажах",
    )
    await pg_session.refresh(request)
    assert request.status == "not_found"
    assert request.fulfillment_shipment_id is None
    assert request.fulfilled_by == archive_user.id
