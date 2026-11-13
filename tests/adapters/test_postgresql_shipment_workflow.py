"""PostgreSQL integration tests for atomic shipment handover and receipt."""
import os
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from medarchive.adapters.postgresql.models import (
    ArchiveRequest, ArchiveShelf, Box, BoxCardMembership, BoxCycle,
    Branch, Card, CardPlacement, Courier, DomainEvent, Location, ShipmentCard, User,
)
from medarchive.services.postgresql_archive_workflow import (
    ArchiveWorkflowError, PostgreSQLArchiveWorkflow,
)
from medarchive.services.postgresql_shipment_workflow import (
    PostgreSQLShipmentWorkflow, ShipmentWorkflowError,
)
from medarchive.services.postgresql_card_custody import (
    CardCustodyWorkflowError, PostgreSQLCardCustodyWorkflow,
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
    # The archive operator is the fulfiller at courier handover; central-office receipt does not overwrite attribution.
    assert request.fulfilled_by == archive_user.id

    # The original archive fulfiller attribution is immutable after handover.
    with pytest.raises(DBAPIError):
        async with pg_session.begin_nested():
            request.fulfilled_by = central_user.id
            await pg_session.flush()


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
async def test_archive_issues_standalone_cards_and_central_office_boxes_them_for_return(pg_session):
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
    archive_box = await workflow.create_box(
        location_id=archive_loc.id, actor_id=archive_user.id,
        card_ids=[card.id for card in cards],
    )
    outbound = await workflow.create_draft(
        from_location_id=archive_loc.id, to_location_id=central_loc.id,
        courier_id=courier.id, issued_by=archive_user.id,
    )
    with pytest.raises(ShipmentWorkflowError, match="только отдельные карты"):
        await workflow.add_box(
            shipment_id=outbound.id, box_id=archive_box.id, actor_id=archive_user.id,
        )
    for card in cards:
        await workflow.remove_card_from_box(
            box_id=archive_box.id, card_id=card.id, actor_id=archive_user.id,
        )
        await workflow.add_standalone_card(
            shipment_id=outbound.id, card_id=card.id, actor_id=archive_user.id,
        )
    await workflow.hand_over(shipment_id=outbound.id, actor_id=archive_user.id)
    await workflow.receive(shipment_id=outbound.id, actor_id=central_user.id)

    for card, request in zip(cards, requests):
        await pg_session.refresh(card)
        await pg_session.refresh(request)
        assert card.current_location_id == central_loc.id
        assert request.status == "completed"

    # Central office creates the box, packs cards, and sends the box to archive.
    box = await workflow.create_box(
        location_id=central_loc.id, actor_id=central_user.id,
        card_ids=[card.id for card in cards],
    )
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
            shipment.handed_over_at = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            )
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

    with pytest.raises(DBAPIError):
        async with pg_session.begin_nested():
            await pg_session.delete(event)
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

    # A stored box is not an active packing surface: its contents are frozen
    # until the archive operator explicitly removes it from the shelf.
    with pytest.raises(ShipmentWorkflowError, match="снимите коробку с архивной полки"):
        await shipping.remove_card_from_box(
            box_id=original_box.id, card_id=card.id, actor_id=archive_user.id,
        )
    with pytest.raises(ShipmentWorkflowError, match="снимите коробку с архивной полки"):
        await shipping.add_card_to_box(
            box_id=original_box.id, card_id=card.id, actor_id=archive_user.id,
        )

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


@pytest.mark.asyncio
async def test_individual_archive_card_can_be_placed_retrieved_issued_and_returned(pg_session):
    (
        branch, _branch_loc, archive_loc, central_loc,
        _branch_user, archive_user, central_user, courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Individually stored patient",
        date_of_birth=__import__("datetime").date(1984, 9, 10),
        origin_branch_id=branch.id, current_location_id=archive_loc.id, status="active",
    )
    shelf = ArchiveShelf(
        section_name="B", shelf_name=f"S-{uuid.uuid4().hex[:8]}", is_active=True,
    )
    pg_session.add_all([card, shelf])
    await pg_session.flush()

    archive = PostgreSQLArchiveWorkflow(pg_session)
    card_id, shelf_id = card.id, shelf.id
    archive_location_id, central_location_id = archive_loc.id, central_loc.id
    archive_user_id, central_user_id, courier_id = archive_user.id, central_user.id, courier.id
    request = (await archive.create_requests(
        card_ids=[card_id], actor_id=central_user_id,
    ))[0]
    first_placement = await archive.place_card(
        card_id=card_id, shelf_id=shelf_id, actor_id=archive_user_id,
    )
    shipping = PostgreSQLShipmentWorkflow(pg_session)
    with pytest.raises(ShipmentWorkflowError, match="снимите карту с архивной полки"):
        async with pg_session.begin_nested():
            await shipping.create_box(
                location_id=archive_location_id, actor_id=archive_user_id,
                card_ids=[card_id],
            )
    outbound = await shipping.create_draft(
        from_location_id=archive_location_id, to_location_id=central_location_id,
        courier_id=courier_id, issued_by=archive_user_id,
    )
    outbound_id = outbound.id
    with pytest.raises(ShipmentWorkflowError, match="снимите карту с архивной полки"):
        async with pg_session.begin_nested():
            await shipping.add_standalone_card(
                shipment_id=outbound_id, card_id=card_id, actor_id=archive_user_id,
            )

    await archive.remove_card_from_shelf(card_id=card_id, actor_id=archive_user_id)
    await shipping.add_standalone_card(
        shipment_id=outbound_id, card_id=card_id, actor_id=archive_user_id,
    )
    await shipping.hand_over(shipment_id=outbound_id, actor_id=archive_user_id)
    await shipping.receive(shipment_id=outbound_id, actor_id=central_user_id)
    await pg_session.refresh(request)
    assert request.status == "completed"
    assert request.fulfillment_shipment_id == outbound_id

    return_shipment = await shipping.create_draft(
        from_location_id=central_location_id, to_location_id=archive_location_id,
        courier_id=courier_id, issued_by=central_user_id,
    )
    await shipping.add_standalone_card(
        shipment_id=return_shipment.id, card_id=card_id, actor_id=central_user_id,
    )
    return_shipment_id = return_shipment.id
    await shipping.hand_over(shipment_id=return_shipment_id, actor_id=central_user_id)
    await shipping.receive(shipment_id=return_shipment_id, actor_id=archive_user_id)
    returned_placement = await archive.place_card(
        card_id=card_id, shelf_id=shelf_id, actor_id=archive_user_id,
    )
    await pg_session.refresh(first_placement)
    assert first_placement.removed_at is not None
    assert returned_placement.removed_at is None
    assert returned_placement.id != first_placement.id
    assert returned_placement.card_id == card_id


@pytest.mark.asyncio
async def test_central_office_external_custody_keeps_card_on_balance_and_blocks_internal_moves(pg_session):
    (
        branch, _branch_loc, archive_loc, central_loc,
        _branch_user, archive_user, central_user, courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="External custody patient",
        date_of_birth=__import__("datetime").date(1982, 3, 4),
        origin_branch_id=branch.id, current_location_id=central_loc.id, status="active",
    )
    pg_session.add(card)
    await pg_session.flush()

    custody = PostgreSQLCardCustodyWorkflow(pg_session)
    await custody.mark_external(
        card_id=card.id, actor_id=central_user.id,
        custody_status="at_court", organization="Районный суд",
        comment="Дело № 123/2026",
    )
    await pg_session.refresh(card)
    assert card.current_location_id == central_loc.id
    assert card.current_shipment_id is None
    assert card.custody_status == "at_court"
    assert card.external_organization == "Районный суд"

    shipping = PostgreSQLShipmentWorkflow(pg_session)
    with pytest.raises(ShipmentWorkflowError, match="внешнюю организацию"):
        await shipping.create_box(
            location_id=central_loc.id, actor_id=central_user.id,
            card_ids=[card.id],
        )
    draft = await shipping.create_draft(
        from_location_id=central_loc.id, to_location_id=archive_loc.id,
        courier_id=courier.id, issued_by=central_user.id,
    )
    with pytest.raises(ShipmentWorkflowError, match="внешнюю организацию"):
        await shipping.add_standalone_card(
            shipment_id=draft.id, card_id=card.id, actor_id=central_user.id,
        )

    await custody.return_to_central_office(
        card_id=card.id, actor_id=central_user.id,
        comment="Оригинал возвращён в ЦО",
    )
    await pg_session.refresh(card)
    assert card.current_location_id == central_loc.id
    assert card.custody_status == "available"
    assert card.external_organization is None
    assert card.external_since is None
    assert card.external_comment == "Оригинал возвращён в ЦО"

    events = list((await pg_session.scalars(
        select(DomainEvent).where(
            DomainEvent.aggregate_type == "card",
            DomainEvent.aggregate_id == card.id,
        ).order_by(DomainEvent.id)
    )).all())
    assert [event.event_type for event in events] == [
        "card.external_custody_started",
        "card.external_custody_returned",
    ]
    assert events[0].payload["organization"] == "Районный суд"
    assert events[1].payload["previous_external_custody"]["comment"] == "Дело № 123/2026"


@pytest.mark.asyncio
async def test_central_office_external_custody_uses_at_external_organization_and_returns_to_balance(pg_session):
    (
        branch, _branch_loc, _archive_loc, central_loc,
        _branch_user, _archive_user, central_user, _courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}",
        full_name="External custody patient",
        date_of_birth=__import__("datetime").date(1987, 7, 9),
        origin_branch_id=branch.id,
        current_location_id=central_loc.id,
        status="active",
    )
    pg_session.add(card)
    await pg_session.flush()

    workflow = PostgreSQLCardCustodyWorkflow(pg_session)
    await workflow.mark_external(
        card_id=card.id,
        actor_id=central_user.id,
        custody_status="at_external_organization",
        organization="Внешняя организация",
        comment="Временная передача",
    )
    await pg_session.refresh(card)
    assert card.custody_status == "at_external_organization"
    assert card.current_location_id == central_loc.id
    assert card.external_organization == "Внешняя организация"

    await workflow.return_to_central_office(
        card_id=card.id, actor_id=central_user.id, comment="Возвращена"
    )
    await pg_session.refresh(card)
    assert card.custody_status == "available"
    assert card.current_location_id == central_loc.id
    assert card.external_organization is None
    assert card.external_since is None


@pytest.mark.asyncio
async def test_archive_cannot_mark_not_found_or_place_card_reserved_in_draft_shipment(pg_session):
    (
        branch, _branch_loc, archive_loc, central_loc,
        _branch_user, archive_user, central_user, courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}",
        full_name="Reserved archive patient",
        date_of_birth=__import__("datetime").date(1986, 4, 12),
        origin_branch_id=branch.id,
        current_location_id=archive_loc.id,
        status="active",
    )
    shelf = ArchiveShelf(
        section_name="C", shelf_name=f"S-{uuid.uuid4().hex[:8]}", is_active=True,
    )
    pg_session.add_all([card, shelf])
    await pg_session.flush()

    archive = PostgreSQLArchiveWorkflow(pg_session)
    request = (await archive.create_requests(
        card_ids=[card.id], actor_id=central_user.id,
    ))[0]
    shipping = PostgreSQLShipmentWorkflow(pg_session)
    shipment = await shipping.create_draft(
        from_location_id=archive_loc.id,
        to_location_id=central_loc.id,
        courier_id=courier.id,
        issued_by=archive_user.id,
    )
    await shipping.add_standalone_card(
        shipment_id=shipment.id, card_id=card.id, actor_id=archive_user.id,
    )

    with pytest.raises(ArchiveWorkflowError, match="активное отправление"):
        await archive.mark_not_found(
            request_id=request.id,
            actor_id=archive_user.id,
            note="Карта не найдена",
        )
    with pytest.raises(ArchiveWorkflowError, match="активное отправление"):
        await archive.place_card(
            card_id=card.id, shelf_id=shelf.id, actor_id=archive_user.id,
        )

    placement_count = await pg_session.scalar(
        select(func.count()).select_from(CardPlacement).where(
            CardPlacement.card_id == card.id,
            CardPlacement.removed_at.is_(None),
        )
    )
    assert placement_count == 0
    await pg_session.refresh(request)
    assert request.status == "new"
    assert request.fulfillment_shipment_id is None

    await shipping.hand_over(shipment_id=shipment.id, actor_id=archive_user.id)
    await shipping.receive(shipment_id=shipment.id, actor_id=central_user.id)
    await pg_session.refresh(request)
    await pg_session.refresh(card)
    assert request.status == "completed"
    assert card.current_location_id == central_loc.id
    assert card.current_shipment_id is None



@pytest.mark.asyncio
async def test_branch_to_central_and_central_to_branch_round_trip_preserves_box_and_card_balance(pg_session):
    (
        branch, branch_loc, _archive_loc, central_loc,
        branch_user, _archive_user, central_user, courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}",
        full_name="Branch round-trip patient",
        date_of_birth=__import__("datetime").date(1992, 8, 14),
        origin_branch_id=branch.id,
        current_location_id=branch_loc.id,
        status="active",
    )
    pg_session.add(card)
    await pg_session.flush()

    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(
        location_id=branch_loc.id,
        actor_id=branch_user.id,
        card_ids=[card.id],
    )

    outbound = await workflow.create_draft(
        from_location_id=branch_loc.id,
        to_location_id=central_loc.id,
        courier_id=courier.id,
        issued_by=branch_user.id,
    )
    await workflow.add_box(
        shipment_id=outbound.id,
        box_id=box.id,
        actor_id=branch_user.id,
    )
    await workflow.hand_over(shipment_id=outbound.id, actor_id=branch_user.id)
    await workflow.receive(shipment_id=outbound.id, actor_id=central_user.id)

    await pg_session.refresh(box)
    await pg_session.refresh(card)
    await pg_session.refresh(outbound)
    assert outbound.status == "received"
    assert box.current_location_id == central_loc.id
    assert box.current_shipment_id is None
    assert card.current_location_id == central_loc.id
    assert card.current_shipment_id is None

    return_shipment = await workflow.create_draft(
        from_location_id=central_loc.id,
        to_location_id=branch_loc.id,
        courier_id=courier.id,
        issued_by=central_user.id,
    )
    await workflow.add_box(
        shipment_id=return_shipment.id,
        box_id=box.id,
        actor_id=central_user.id,
    )
    await workflow.hand_over(
        shipment_id=return_shipment.id,
        actor_id=central_user.id,
    )
    await workflow.receive(
        shipment_id=return_shipment.id,
        actor_id=branch_user.id,
    )

    await pg_session.refresh(box)
    await pg_session.refresh(card)
    await pg_session.refresh(return_shipment)
    assert return_shipment.status == "received"
    assert box.current_location_id == branch_loc.id
    assert box.current_shipment_id is None
    assert card.current_location_id == branch_loc.id
    assert card.current_shipment_id is None

    events = list((await pg_session.scalars(
        select(DomainEvent).where(
            DomainEvent.aggregate_type == "card",
            DomainEvent.aggregate_id == card.id,
        ).order_by(DomainEvent.id)
    )).all())
    assert [event.event_type for event in events] == [
        "card.added_to_box",
        "card.shipment_handed_over",
        "card.shipment_received",
        "card.shipment_handed_over",
        "card.shipment_received",
    ]


@pytest.mark.asyncio
async def test_multiple_location_locks_are_acquired_in_stable_order():
    workflow = PostgreSQLShipmentWorkflow(session=None)
    first_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    second_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    acquired = []

    async def fake_location(location_id):
        acquired.append(location_id)
        return Location(
            id=location_id, kind="branch", branch_id=uuid.uuid4(),
            name=str(location_id), is_active=True,
        )

    workflow._location = fake_location
    first, second = await workflow._locations(first_id, second_id)

    assert acquired == [second_id, first_id]
    assert first.id == first_id
    assert second.id == second_id


@pytest.mark.asyncio
async def test_register_shipment_exception_preserves_cargo_and_manifest(pg_session):
    (
        branch, branch_loc, archive_loc, _central_loc,
        branch_user, archive_user, _central_user, courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Incident patient",
        date_of_birth=__import__("datetime").date(1985, 3, 4),
        origin_branch_id=branch.id, current_location_id=branch_loc.id, status="active",
    )
    pg_session.add(card)
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

    await workflow.register_exception(
        shipment_id=shipment.id, actor_id=archive_user.id,
        note="Повреждена транспортная упаковка",
    )
    await pg_session.refresh(shipment)
    await pg_session.refresh(card)
    manifest_row = await pg_session.scalar(
        select(ShipmentCard).where(
            ShipmentCard.shipment_id == shipment.id,
            ShipmentCard.card_id == card.id,
        )
    )
    event = await pg_session.scalar(
        select(DomainEvent).where(
            DomainEvent.event_type == "shipment.exception_registered",
            DomainEvent.aggregate_id == shipment.id,
        )
    )
    assert shipment.status == "exception"
    assert shipment.exception_by == archive_user.id
    assert shipment.exception_at is not None
    assert shipment.exception_note == "Повреждена транспортная упаковка"
    assert card.current_location_id is None
    assert card.current_shipment_id == shipment.id
    assert manifest_row is not None and manifest_row.box_id is None
    assert event is not None
    assert event.payload["note"] == "Повреждена транспортная упаковка"


@pytest.mark.asyncio
async def test_register_shipment_exception_rejects_blank_note_wrong_actor_and_wrong_status(pg_session):
    (
        branch, branch_loc, archive_loc, _central_loc,
        branch_user, _archive_user, _central_user, courier,
    ) = await seed_locations(pg_session)
    admin = User(
        username=f"admin-{uuid.uuid4().hex}", password_hash="x",
        full_name="Administrator", role="admin", branch_id=None, is_active=True,
    )
    pg_session.add(admin)
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    draft = await workflow.create_draft(
        from_location_id=branch_loc.id, to_location_id=archive_loc.id,
        courier_id=courier.id, issued_by=branch_user.id,
    )
    with pytest.raises(ShipmentWorkflowError, match="в пути"):
        await workflow.register_exception(
            shipment_id=draft.id, actor_id=branch_user.id, note="Проверка",
        )

    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Incident guard patient",
        date_of_birth=__import__("datetime").date(1986, 4, 5),
        origin_branch_id=branch.id, current_location_id=branch_loc.id, status="active",
    )
    pg_session.add(card)
    await pg_session.flush()
    transit = await workflow.create_draft(
        from_location_id=branch_loc.id, to_location_id=archive_loc.id,
        courier_id=courier.id, issued_by=branch_user.id,
    )
    await workflow.add_standalone_card(
        shipment_id=transit.id, card_id=card.id, actor_id=branch_user.id,
    )
    await workflow.hand_over(shipment_id=transit.id, actor_id=branch_user.id)

    with pytest.raises(ShipmentWorkflowError, match="причину"):
        await workflow.register_exception(
            shipment_id=transit.id, actor_id=branch_user.id, note="   ",
        )
    with pytest.raises(ShipmentWorkflowError, match="сотрудник места"):
        await workflow.register_exception(
            shipment_id=transit.id, actor_id=admin.id, note="Нет доступа",
        )
    await pg_session.refresh(transit)
    await pg_session.refresh(card)
    assert transit.status == "in_transit"
    assert transit.exception_by is None
    assert card.current_shipment_id == transit.id


@pytest.mark.asyncio
async def test_exception_is_resolved_only_by_receiving_the_complete_verified_manifest(pg_session):
    (
        branch, branch_loc, archive_loc, _central_loc,
        branch_user, archive_user, _central_user, courier,
    ) = await seed_locations(pg_session)
    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Recovered incident patient",
        date_of_birth=__import__("datetime").date(1987, 5, 6),
        origin_branch_id=branch.id, current_location_id=branch_loc.id, status="active",
    )
    pg_session.add(card)
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(
        location_id=branch_loc.id, actor_id=branch_user.id, card_ids=[card.id],
    )
    shipment = await workflow.create_draft(
        from_location_id=branch_loc.id, to_location_id=archive_loc.id,
        courier_id=courier.id, issued_by=branch_user.id,
    )
    await workflow.add_box(
        shipment_id=shipment.id, box_id=box.id, actor_id=branch_user.id,
    )
    await workflow.hand_over(shipment_id=shipment.id, actor_id=branch_user.id)
    await workflow.register_exception(
        shipment_id=shipment.id, actor_id=archive_user.id,
        note="Отправление временно не было обнаружено",
    )

    await workflow.resolve_exception(
        shipment_id=shipment.id, actor_id=archive_user.id,
        resolution_note="Коробка и карта найдены; номер и полный состав сверены с манифестом",
    )

    await pg_session.refresh(shipment)
    await pg_session.refresh(box)
    await pg_session.refresh(card)
    assert shipment.status == "received"
    assert shipment.received_by == archive_user.id
    assert shipment.received_at is not None
    assert shipment.exception_by == archive_user.id
    assert shipment.exception_at is not None
    assert shipment.exception_note == "Отправление временно не было обнаружено"
    assert box.current_location_id == archive_loc.id
    assert box.current_shipment_id is None
    assert card.current_location_id == archive_loc.id
    assert card.current_shipment_id is None

    event = await pg_session.scalar(
        select(DomainEvent).where(
            DomainEvent.event_type == "shipment.exception_resolved",
            DomainEvent.aggregate_id == shipment.id,
        )
    )
    assert event is not None
    assert event.actor_user_id == archive_user.id
    assert event.payload["resolution"] == "complete_manifest_verified"
    assert "полный состав" in event.payload["resolution_note"]
    assert event.payload["original_exception_note"] == shipment.exception_note


@pytest.mark.asyncio
async def test_exception_resolution_requires_recipient_and_valid_incident(pg_session):
    (
        _branch, branch_loc, archive_loc, _central_loc,
        branch_user, archive_user, _central_user, courier,
    ) = await seed_locations(pg_session)
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    shipment = await workflow.create_draft(
        from_location_id=branch_loc.id, to_location_id=archive_loc.id,
        courier_id=courier.id, issued_by=branch_user.id,
    )
    with pytest.raises(ShipmentWorkflowError, match="результат проверки"):
        await workflow.resolve_exception(
            shipment_id=shipment.id, actor_id=archive_user.id, resolution_note=" ",
        )
    with pytest.raises(ShipmentWorkflowError, match="разрешён"):
        await workflow.resolve_exception(
            shipment_id=shipment.id, actor_id=archive_user.id,
            resolution_note="Проверено",
        )

    card = Card(
        card_number=f"C-{uuid.uuid4().hex}", full_name="Wrong resolver patient",
        date_of_birth=__import__("datetime").date(1988, 6, 7),
        origin_branch_id=_branch.id, current_location_id=branch_loc.id, status="active",
    )
    pg_session.add(card)
    await pg_session.flush()
    await workflow.add_standalone_card(
        shipment_id=shipment.id, card_id=card.id, actor_id=branch_user.id,
    )
    await workflow.hand_over(shipment_id=shipment.id, actor_id=branch_user.id)
    await workflow.register_exception(
        shipment_id=shipment.id, actor_id=archive_user.id, note="Нужна проверка груза",
    )
    with pytest.raises(ShipmentWorkflowError, match="Недостаточно прав"):
        await workflow.resolve_exception(
            shipment_id=shipment.id, actor_id=branch_user.id,
            resolution_note="Груз проверен",
        )
    await pg_session.refresh(shipment)
    await pg_session.refresh(card)
    assert shipment.status == "exception"
    assert card.current_shipment_id == shipment.id
    assert card.current_location_id is None



@pytest.mark.asyncio
async def test_empty_box_can_be_written_off_and_cycle_history_is_closed(pg_session):
    (_branch, branch_loc, archive_loc, central_loc,
     branch_user, archive_user, central_user, _courier) = await seed_locations(pg_session)
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    for location, actor in ((branch_loc, branch_user), (central_loc, central_user), (archive_loc, archive_user)):
        box = await workflow.create_box(location_id=location.id, actor_id=actor.id)
        cycle = await pg_session.scalar(select(BoxCycle).where(BoxCycle.box_id == box.id, BoxCycle.ended_at.is_(None)))
        written_off = await workflow.write_off_box(box_id=box.id, actor_id=actor.id)
        await pg_session.refresh(written_off); await pg_session.refresh(cycle)
        assert written_off.status == "written_off" and written_off.current_location_id == location.id
        assert written_off.current_shipment_id is None
        assert cycle.ended_at is not None and cycle.ended_by == actor.id
        event = await pg_session.scalar(select(DomainEvent).where(
            DomainEvent.event_type == "box.written_off", DomainEvent.aggregate_id == box.id))
        assert event is not None and event.payload["location_id"] == str(location.id)
        with pytest.raises(ShipmentWorkflowError, match="уже списана"):
            await workflow.write_off_box(box_id=box.id, actor_id=actor.id)
        with pytest.raises(ShipmentWorkflowError):
            await workflow.add_card_to_box(box_id=box.id, card_id=uuid.uuid4(), actor_id=actor.id)


@pytest.mark.asyncio
async def test_nonempty_box_cannot_be_written_off(pg_session):
    branch, branch_loc, _archive, _central, branch_user, *_ = await seed_locations(pg_session)
    card = Card(card_number=f"C-{uuid.uuid4().hex}", full_name="Patient",
        date_of_birth=__import__("datetime").date(1980, 1, 1),
        origin_branch_id=branch.id, current_location_id=branch_loc.id, status="active")
    pg_session.add(card); await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(location_id=branch_loc.id, actor_id=branch_user.id)
    await workflow.add_card_to_box(box_id=box.id, card_id=card.id, actor_id=branch_user.id)
    with pytest.raises(ShipmentWorkflowError, match="только пустую"):
        await workflow.write_off_box(box_id=box.id, actor_id=branch_user.id)
    await pg_session.refresh(box); assert box.status == "active"


@pytest.mark.asyncio
async def test_box_in_active_shipment_cannot_be_written_off(pg_session):
    branch, branch_loc, archive_loc, _central, branch_user, _archive_user, _central_user, courier = await seed_locations(pg_session)
    card = Card(card_number=f"C-{uuid.uuid4().hex}", full_name="Patient",
        date_of_birth=__import__("datetime").date(1980, 1, 1),
        origin_branch_id=branch.id, current_location_id=branch_loc.id, status="active")
    pg_session.add(card); await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(location_id=branch_loc.id, actor_id=branch_user.id)
    await workflow.add_card_to_box(box_id=box.id, card_id=card.id, actor_id=branch_user.id)
    shipment = await workflow.create_draft(from_location_id=branch_loc.id, to_location_id=archive_loc.id,
        courier_id=courier.id, issued_by=branch_user.id)
    await workflow.add_box(shipment_id=shipment.id, box_id=box.id, actor_id=branch_user.id)
    with pytest.raises(ShipmentWorkflowError, match="активное отправление"):
        await workflow.write_off_box(box_id=box.id, actor_id=branch_user.id)
    await pg_session.refresh(box); assert box.status == "active"


@pytest.mark.asyncio
async def test_box_emptied_by_removing_last_card_can_be_written_off(pg_session):
    branch, branch_loc, _archive, _central, branch_user, *_ = await seed_locations(pg_session)
    card = Card(card_number=f"C-{uuid.uuid4().hex}", full_name="Patient",
        date_of_birth=__import__("datetime").date(1980, 1, 1),
        origin_branch_id=branch.id, current_location_id=branch_loc.id, status="active")
    pg_session.add(card); await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(location_id=branch_loc.id, actor_id=branch_user.id)
    await workflow.add_card_to_box(box_id=box.id, card_id=card.id, actor_id=branch_user.id)
    await workflow.remove_card_from_box(box_id=box.id, card_id=card.id, actor_id=branch_user.id)
    await workflow.write_off_box(box_id=box.id, actor_id=branch_user.id)
    await pg_session.refresh(box)
    assert box.status == "written_off"
