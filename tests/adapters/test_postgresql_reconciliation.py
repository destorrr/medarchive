"""E3 inventory regressions in the existing rollback-only Foundation fixture."""
from dataclasses import asdict
from uuid import uuid4

import pytest
from sqlalchemy import text

from medarchive.adapters.postgresql.reconciliation import inspect_reconciliation
from tests.adapters.test_postgresql_shipment_workflow import pg_session, seed_locations


@pytest.mark.asyncio
async def test_e3_inventory_returns_counts_without_changes(pg_session):
    connection = await pg_session.connection()
    before = await inspect_reconciliation(connection)
    assert all(type(value) is int and value >= 0 for value in asdict(before).values())
    assert before == await inspect_reconciliation(connection)
    assert before.requires_review == any(asdict(before).values())


@pytest.mark.asyncio
async def test_e3_inventory_detects_legacy_roles_and_duplicate_couriers(pg_session):
    connection = await pg_session.connection()
    before = await inspect_reconciliation(connection)
    marker = uuid4().hex
    await connection.execute(text("""
        INSERT INTO public.users (username, full_name, password_hash, role)
        VALUES (:name, 'Synthetic viewer', 'test-only', 'viewer')
    """), {"name": marker})
    await connection.execute(text("""
        INSERT INTO public.couriers (full_name, is_active)
        VALUES (:first, true), (:second, false)
    """), {"first": marker + " Test", "second": "\t" + marker.upper() + "  TEST\n"})
    after = await inspect_reconciliation(connection)
    expected = asdict(before)
    expected["viewer_accounts"] += 1
    expected["courier_duplicate_groups"] += 1
    assert asdict(after) == expected
    assert after.requires_review
    assert after == await inspect_reconciliation(connection)


@pytest.mark.asyncio
async def test_e3_inventory_detects_unmapped_request_and_individual_placement(pg_session):
    from medarchive.adapters.postgresql.models import ArchiveRequest, ArchiveShelf, Card, CardPlacement
    from datetime import date

    branch, _, archive, _, _, archivist, central_user, _ = await seed_locations(pg_session)
    connection = await pg_session.connection()
    before = await inspect_reconciliation(connection)
    card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                current_location_id=archive.id, status="active")
    shelf = ArchiveShelf(section_name="Synthetic", shelf_name=uuid4().hex)
    pg_session.add_all([card, shelf])
    await pg_session.flush()
    pg_session.add_all([
        ArchiveRequest(card_id=card.id, requested_by=central_user.id, status="new"),
        CardPlacement(card_id=card.id, shelf_id=shelf.id, placed_by=archivist.id),
    ])
    await pg_session.flush()
    after = await inspect_reconciliation(connection)
    expected = asdict(before)
    expected["legacy_requests"] += 1
    expected["active_individual_placements"] += 1
    assert asdict(after) == expected
    assert after.requires_review



@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind,destination_kind", [
    ("branch", "archive"), ("central_office", "archive"),
    ("branch", "central_office"), ("central_office", "branch"),
])
async def test_e3_inventory_ordinary_and_excluded_routes(
    pg_session, source_kind, destination_kind,
):
    from datetime import date
    from medarchive.adapters.postgresql.models import Card
    from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow

    branch, branch_loc, archive_loc, central_loc, branch_user, _, central_user, courier = (
        await seed_locations(pg_session)
    )
    locations = {"branch": branch_loc, "archive": archive_loc, "central_office": central_loc}
    sender = branch_user if source_kind == "branch" else central_user
    source, destination = locations[source_kind], locations[destination_kind]
    connection = await pg_session.connection()
    before = await inspect_reconciliation(connection)
    card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                current_location_id=source.id, status="active")
    pg_session.add(card)
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(location_id=source.id, actor_id=sender.id)
    await workflow.add_card_to_box(box_id=box.id, card_id=card.id, actor_id=sender.id)
    # Use the legacy schema's allowed draft route, not a new business command.
    # No handover or guard bypass is needed to inventory legacy draft rows.
    shipment_id = (await connection.execute(text("""
        INSERT INTO public.shipments (from_location_id, to_location_id, courier_id, issued_by)
        VALUES (:source, :destination, :courier, :sender) RETURNING id
    """), {"source": source.id, "destination": destination.id,
           "courier": courier.id, "sender": sender.id})).scalar_one()
    await connection.execute(text("""
        INSERT INTO public.shipment_boxes (shipment_id, box_id) VALUES (:shipment, :box)
    """), {"shipment": shipment_id, "box": box.id})
    await connection.execute(text("""
        INSERT INTO public.shipment_cards (shipment_id, card_id, box_id)
        VALUES (:shipment, :card, :box)
    """), {"shipment": shipment_id, "card": card.id, "box": box.id})
    expected = asdict(before)
    expected["legacy_box_manifest_cards"] += 1
    expected["excluded_route_shipments"] += int(destination_kind != "archive")
    assert asdict(await inspect_reconciliation(connection)) == expected


@pytest.mark.asyncio
async def test_e3_inventory_detects_inconsistent_additive_item_link(pg_session):
    from datetime import date
    from medarchive.adapters.postgresql.models import ArchiveRequest, Card
    from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow

    branch, _, archive, central, _, archivist, central_user, courier = (
        await seed_locations(pg_session)
    )
    connection = await pg_session.connection()
    before = await inspect_reconciliation(connection)
    cards = [Card(card_number=uuid4().hex, full_name="Synthetic patient",
                  date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                  current_location_id=archive.id, status="active") for _ in range(2)]
    pg_session.add_all(cards)
    await pg_session.flush()
    request = ArchiveRequest(card_id=cards[0].id, requested_by=central_user.id, status="new")
    pg_session.add(request)
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    shipment = await workflow.create_draft(
        from_location_id=archive.id, to_location_id=central.id,
        courier_id=courier.id, issued_by=archivist.id,
    )
    item_id = (await connection.execute(text("""
        INSERT INTO public.archive_request_items
            (request_id, card_id, status, reservation_active, added_at, added_by)
        VALUES (:request, :card, 'pending', true, :added_at, :actor)
        RETURNING id
    """), {"request": request.id, "card": cards[0].id,
           "added_at": request.created_at, "actor": central_user.id})).scalar_one()
    # Additive FK alone permits this mismatch; detect it without weakening guards.
    await connection.execute(text("""
        UPDATE public.shipments SET request_id = :request WHERE id = :shipment
    """), {"request": request.id, "shipment": shipment.id})
    await connection.execute(text("""
        INSERT INTO public.shipment_cards (shipment_id, card_id, request_item_id)
        VALUES (:shipment, :card, :item)
    """), {"shipment": shipment.id, "card": cards[1].id, "item": item_id})
    expected = asdict(before)
    expected["conflicting_item_links"] += 1
    assert asdict(await inspect_reconciliation(connection)) == expected


@pytest.mark.asyncio
async def test_e3_inventory_detects_shared_legacy_fulfillment_and_conflicting_link(pg_session):
    from datetime import date
    from medarchive.adapters.postgresql.models import ArchiveRequest, Card
    from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow

    branch, _, archive, central, _, archivist, central_user, courier = (
        await seed_locations(pg_session)
    )
    connection = await pg_session.connection()
    before = await inspect_reconciliation(connection)
    cards = [Card(card_number=uuid4().hex, full_name="Synthetic patient",
                  date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                  current_location_id=archive.id, status="active") for _ in range(2)]
    pg_session.add_all(cards)
    await pg_session.flush()
    requests = [ArchiveRequest(card_id=card.id, requested_by=central_user.id, status="new")
                for card in cards]
    pg_session.add_all(requests)
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    shipment = await workflow.create_draft(
        from_location_id=archive.id, to_location_id=central.id,
        courier_id=courier.id, issued_by=archivist.id,
    )
    for card in cards:
        await workflow.add_standalone_card(
            shipment_id=shipment.id, card_id=card.id, actor_id=archivist.id,
        )
    await workflow.hand_over(shipment_id=shipment.id, actor_id=archivist.id)
    await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    expected = asdict(before)
    expected["legacy_requests"] += 2
    expected["shared_legacy_fulfillments"] += 1
    assert asdict(await inspect_reconciliation(connection)) == expected
    # The new nullable parent link is not sufficient to resolve shared old requests.
    await connection.execute(text("""
        UPDATE public.shipments SET request_id = :request WHERE id = :shipment
    """), {"request": requests[0].id, "shipment": shipment.id})
    expected["conflicting_request_links"] += 1
    assert asdict(await inspect_reconciliation(connection)) == expected


@pytest.mark.asyncio
async def test_e3_inventory_detects_inactive_shelf_with_existing_box(pg_session):
    from datetime import date
    from medarchive.adapters.postgresql.models import ArchiveShelf, Card
    from medarchive.services.postgresql_archive_workflow import PostgreSQLArchiveWorkflow
    from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow

    branch, _, archive, _, _, archivist, _, _ = await seed_locations(pg_session)
    connection = await pg_session.connection()
    before = await inspect_reconciliation(connection)
    card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                current_location_id=archive.id, status="active")
    shelf = ArchiveShelf(section_name="Synthetic", shelf_name=uuid4().hex)
    pg_session.add_all([card, shelf])
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(location_id=archive.id, actor_id=archivist.id)
    await workflow.add_card_to_box(box_id=box.id, card_id=card.id, actor_id=archivist.id)
    await PostgreSQLArchiveWorkflow(pg_session).place_box(
        box_id=box.id, shelf_id=shelf.id, actor_id=archivist.id,
    )
    await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await inspect_reconciliation(connection) == before
    # Legacy schema does not recheck existing placements on shelf deactivation.
    await connection.execute(text("""
        UPDATE public.archive_shelves SET is_active = false WHERE id = :shelf
    """), {"shelf": shelf.id})
    expected = asdict(before)
    expected["conflicting_active_box_placements"] += 1
    assert asdict(await inspect_reconciliation(connection)) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["closed_cycle", "location_mismatch"])
async def test_e3_inventory_detects_membership_conflict_before_deferred_rejection(pg_session, conflict):
    from datetime import date
    from sqlalchemy.exc import DBAPIError
    from medarchive.adapters.postgresql.models import Card
    from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow

    branch, branch_loc, archive, _, branch_user, _, _, _ = await seed_locations(pg_session)
    connection = await pg_session.connection()
    before = await inspect_reconciliation(connection)
    card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                current_location_id=branch_loc.id, status="active")
    pg_session.add(card)
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(location_id=branch_loc.id, actor_id=branch_user.id)
    await workflow.add_card_to_box(box_id=box.id, card_id=card.id, actor_id=branch_user.id)
    assert await inspect_reconciliation(connection) == before
    # Inspect the inconsistent intermediate state inside a savepoint, then
    # demonstrate the existing deferred constraint rejects it. Never commit it.
    with pytest.raises(DBAPIError) as error:
        async with connection.begin_nested():
            if conflict == "closed_cycle":
                await connection.execute(text("""
                    UPDATE public.box_cycles SET ended_at = now()
                    WHERE box_id = :box AND ended_at IS NULL
                """), {"box": box.id})
            else:
                await connection.execute(text("""
                    UPDATE public.cards SET current_location_id = :archive WHERE id = :card
                """), {"archive": archive.id, "card": card.id})
            expected = asdict(before)
            expected["conflicting_active_memberships"] += 1
            assert asdict(await inspect_reconciliation(connection)) == expected
            await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert error.value.orig.sqlstate == "23514"
    assert await inspect_reconciliation(connection) == before


@pytest.mark.asyncio
async def test_cutover_review_clean_and_legacy_directory_conflict(pg_session):
    from medarchive.adapters.postgresql.reconciliation import inspect_cutover_review

    connection = await pg_session.connection()
    before = await inspect_cutover_review(connection)
    assert before.clear
    marker = uuid4().hex
    await connection.execute(text("""
        INSERT INTO public.users (username, full_name, password_hash, role)
        VALUES (:name, 'Synthetic viewer', 'test-only', 'viewer')
    """), {"name": marker})
    await connection.execute(text("""
        INSERT INTO public.couriers (full_name)
        VALUES (:first), (:second)
    """), {"first": marker + " Test", "second": marker.upper() + " TEST"})
    review = await inspect_cutover_review(connection)
    assert not review.clear
    assert dict(review.blockers) == {"viewer_accounts": 1, "courier_duplicate_groups": 1}
    assert review == await inspect_cutover_review(connection)


@pytest.mark.asyncio
async def test_cutover_review_requires_pending_backfill_and_detects_item_conflict(pg_session):
    from datetime import date
    from medarchive.adapters.postgresql.models import ArchiveRequest, Card
    from medarchive.adapters.postgresql.reconciliation import inspect_cutover_review
    from medarchive.adapters.postgresql.request_reconciliation import backfill_pending_request_items
    from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow

    branch, _, archive, _, _, archivist, central_user, _ = await seed_locations(pg_session)
    connection = await pg_session.connection()
    assert (await inspect_cutover_review(connection)).clear
    card = Card(card_number=uuid4().hex, full_name="Synthetic patient",
                date_of_birth=date(1980, 1, 1), origin_branch_id=branch.id,
                current_location_id=archive.id, status="active")
    pg_session.add(card)
    await pg_session.flush()
    workflow = PostgreSQLShipmentWorkflow(pg_session)
    box = await workflow.create_box(location_id=archive.id, actor_id=archivist.id)
    await workflow.add_card_to_box(box_id=box.id, card_id=card.id, actor_id=archivist.id)
    request = ArchiveRequest(card_id=card.id, requested_by=central_user.id, status="new")
    pg_session.add(request)
    await pg_session.flush()
    review = await inspect_cutover_review(connection)
    assert dict(review.blockers) == {"legacy_requests": 1, "pending_request_backfill": 1}
    assert await backfill_pending_request_items(connection) == 1
    assert (await inspect_cutover_review(connection)).clear
    await connection.execute(text("""
        UPDATE public.archive_request_items SET reservation_active = false
        WHERE request_id = :request
    """), {"request": request.id})
    review = await inspect_cutover_review(connection)
    assert not review.clear
    assert dict(review.blockers) == {"unresolved_request_mappings": 1}
